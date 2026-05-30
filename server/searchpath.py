import asyncio
import io
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Annotated, List, Optional, Tuple

import httpx
import numpy as np
import pandas as pd
import redis.asyncio as redis
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from sklearn.neighbors import BallTree
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

# ─────────────────────────────────────────────
# 系統設定與 Redis 初始化
# ─────────────────────────────────────────────
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
redis_client = redis.from_url(REDIS_URL, decode_responses=True)

# 建立全局 HTTP Client 以重複利用 TCP 連線
http_client = httpx.AsyncClient(timeout=10.0)

# FastAPI 生命週期管理 (確保關閉時釋放資源)
@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await http_client.aclose()
    await redis_client.aclose()

# ─────────────────────────────────────────────
# 應用程式初始化
# ─────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="空污共犯 - 健康路徑規劃子系統 API", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ─────────────────────────────────────────────
# CORS 設定
# [修正②] 明確列出允許的 methods 與 headers，
#          避免 allow_credentials=True 搭配萬用字元的安全風險
# ─────────────────────────────────────────────
origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "https://pm-2-5-map.vercel.app",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)

# ─────────────────────────────────────────────
# 常數定義
# [修正⑨] CSV_URL 改由環境變數讀取，避免 Sheet ID 變動需重新部署
# ─────────────────────────────────────────────
CSV_URL = os.getenv(
    "SENSOR_CSV_URL",
    "https://docs.google.com/spreadsheets/d/"
    "1jcRopeeqnT786iB9m6Jd8oQH34S2AUE9Sp5-4eCgwYQ/export?format=csv",
)

SEARCH_RADIUS_RADIANS = 6.4 / 6371.0
CACHE_TTL = timedelta(minutes=5)

# ─────────────────────────────────────────────
# Pydantic 資料型態定義
# [修正④⑧] 在 schema 層強制規範座標值域與順序，
#           取代執行時的啟發式翻轉邏輯
# ─────────────────────────────────────────────
Latitude  = Annotated[float, Field(ge=-90.0,  le=90.0)]
Longitude = Annotated[float, Field(ge=-180.0, le=180.0)]

class Coordinate(BaseModel):
    lat: Latitude
    lon: Longitude

class RouteGeometry(BaseModel):
    route_id: str
    # 前端請傳入 {"lat": ..., "lon": ...} 物件陣列，座標順序由 schema 保證
    coordinates: List[Coordinate]

    @field_validator("coordinates")
    @classmethod
    def must_have_coordinates(cls, v: List[Coordinate]) -> List[Coordinate]:
        if not v:
            raise ValueError("座標陣列不得為空")
        return v

class HealthRoutingRequest(BaseModel):
    routes: List[RouteGeometry]

# ─────────────────────────────────────────────
# 本地記憶體快取（單 worker 模式適用）
# [修正⑩] 已在程式碼與部署說明中標注多 worker 限制
# NOTE: 若以 uvicorn --workers N (N>1) 部署，各 process 快取獨立，
#       可能造成資料版本不一致。建議生產環境以單 worker + async 處理，
#       或移除本地快取、完全依賴 Redis 共享層。
# ─────────────────────────────────────────────
_local_cache: dict = {
    "df": None,
    "tree": None,
    "updated_at": None,
}

# [修正⑤] 使用 asyncio.Lock 防止快取過期時多個 coroutine 同時打穿到 Google Sheets
_cache_lock = asyncio.Lock()

# ─────────────────────────────────────────────
# 感測器資料抓取與快取（雙層快取架構）
# ─────────────────────────────────────────────
async def fetch_sensor_data_and_build_tree() -> Tuple[pd.DataFrame, BallTree]:
    now = datetime.now()

    # 快速路徑：本地快取有效，直接回傳（不需加鎖）
    if (
        _local_cache["df"] is not None
        and _local_cache["updated_at"] is not None
        and (now - _local_cache["updated_at"]) < CACHE_TTL
    ):
        return _local_cache["df"], _local_cache["tree"]

    # 慢速路徑：加鎖後再次確認，避免驚群（thundering herd）
    async with _cache_lock:
        # 二次確認：可能在等鎖期間已被其他 coroutine 更新
        now = datetime.now()
        if (
            _local_cache["df"] is not None
            and _local_cache["updated_at"] is not None
            and (now - _local_cache["updated_at"]) < CACHE_TTL
        ):
            return _local_cache["df"], _local_cache["tree"]

        csv_data: Optional[str] = None

        # [修正⑦] Redis 操作包裹 try/except，連線失敗時降級至直接呼叫來源
        try:
            csv_data = await redis_client.get("sensor_data_csv")
        except Exception as e:
            print(f"Redis 讀取失敗，降級為直接抓取來源: {str(e)}")

        if not csv_data:
            try:
                response = await http_client.get(CSV_URL)
                response.raise_for_status()
                csv_data = response.text

                try:
                    await redis_client.set("sensor_data_csv", csv_data, ex=300)
                except Exception as e:
                    print(f"Redis 寫入失敗（不影響服務）: {str(e)}")

            except Exception as e:
                print(f"從 Google Sheets 獲取失敗: {str(e)}")
                raise HTTPException(status_code=502, detail="無法同步感測網路資料")

        df = pd.read_csv(io.StringIO(csv_data))
        df = df.dropna(subset=["latitude", "longitude", "pm25"])

        if df.empty:
            raise ValueError("資料來源內無有效感測數據")

        coords_rad = np.radians(df[["latitude", "longitude"]].values)
        spatial_tree = BallTree(coords_rad, metric="haversine")

        _local_cache["df"] = df
        _local_cache["tree"] = spatial_tree
        _local_cache["updated_at"] = datetime.now()

    return _local_cache["df"], _local_cache["tree"]

# ─────────────────────────────────────────────
# 路由端點
# ─────────────────────────────────────────────
@app.get("/")
def health_check():
    return {"status": "online", "message": "空污共犯 API 伺服器運作中！"}


@app.get("/api/geocode")
@limiter.limit("5/second")
async def geocode_address(request: Request, q: str):
    if not q or not q.strip():
        raise HTTPException(status_code=400, detail="請提供查詢地址")

    query = q.strip()
    cache_key = f"geocode:{query}"

    # [修正⑦] Redis 快取讀取降級處理
    try:
        cached_result = await redis_client.get(cache_key)
        if cached_result:
            return json.loads(cached_result)
    except Exception as e:
        print(f"Redis geocode 快取讀取失敗，略過快取: {str(e)}")

    # ── 第一層：Google Maps Geocoding API ──
    # [修正③] 額外 strip() 確保空白字串不被當作有效 key
    GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()
    if GOOGLE_MAPS_API_KEY:
        try:
            url = "https://maps.googleapis.com/maps/api/geocode/json"
            params = {
                "address": query,
                "key": GOOGLE_MAPS_API_KEY,
                "language": "zh-TW",
                "region": "tw",
            }
            response = await http_client.get(url, params=params)
            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "OK" and data.get("results"):
                    location = data["results"][0]["geometry"]["location"]
                    result = {"lat": location["lat"], "lon": location["lng"], "source": "google"}
                    await _try_cache_set(cache_key, result, ex=86400)
                    return result
        except Exception as e:
            print(f"Google Geocoding 服務異常，切換至備援引擎: {str(e)}")

    # ── 第二層：ArcGIS REST API ──
    try:
        arcgis_url = (
            "https://geocode.arcgis.com/arcgis/rest/services"
            "/World/GeocodeServer/findAddressCandidates"
        )
        params = {
            "singleLine": query,
            "f": "json",
            "maxLocations": 1,
            "outSR": 4326,
        }
        response = await http_client.get(arcgis_url, params=params)
        if response.status_code == 200:
            data = response.json()
            candidates = data.get("candidates", [])
            if candidates:
                location = candidates[0]["location"]
                result = {"lat": location["y"], "lon": location["x"], "source": "arcgis"}
                await _try_cache_set(cache_key, result, ex=86400)
                return result
    except Exception as e:
        print(f"ArcGIS Geocoding 服務異常，切換至最終備援引擎: {str(e)}")

    # ── 第三層：Photon API（OpenStreetMap）──
    try:
        photon_url = "https://photon.komoot.io/api/"
        params = {"q": query, "limit": 1, "lang": "en"}
        response = await http_client.get(photon_url, params=params)
        if response.status_code == 200:
            data = response.json()
            features = data.get("features", [])
            if features:
                # Photon 回傳格式為 [lng, lat]
                coordinates = features[0]["geometry"]["coordinates"]
                result = {"lat": coordinates[1], "lon": coordinates[0], "source": "photon"}
                await _try_cache_set(cache_key, result, ex=86400)
                return result
    except Exception as e:
        print(f"Photon Geocoding 服務異常: {str(e)}")

    raise HTTPException(status_code=404, detail=f"所有地理編碼引擎皆無法解析「{query}」")


async def _try_cache_set(key: str, value: dict, ex: int) -> None:
    """將結果寫入 Redis；失敗時靜默降級，不影響主流程。"""
    try:
        await redis_client.set(key, json.dumps(value), ex=ex)
    except Exception as e:
        print(f"Redis 快取寫入失敗（不影響服務）: {str(e)}")


@app.post("/api/calculate-health-routes")
# [修正①] 為高成本的運算端點加上 rate limit 防止 DoS
@limiter.limit("10/minute")
async def calculate_health_routes(request: Request, payload: HealthRoutingRequest):
    if not payload.routes:
        raise HTTPException(status_code=400, detail="請提供至少一條待評估路線")

    df_sensors, spatial_tree = await fetch_sensor_data_and_build_tree()
    sensor_pm25_values = df_sensors["pm25"].values

    evaluated_results = []

    for route in payload.routes:
        # coordinates 已由 Pydantic schema 保證非空且格式正確（lat/lon 物件）
        # [修正④] 不再需要啟發式座標翻轉，直接從具名欄位取值
        route_points = np.array([[c.lat, c.lon] for c in route.coordinates])
        route_points_rad = np.radians(route_points)

        # 批次查詢整條路線所有節點半徑內的所有測站索引
        indices_list = spatial_tree.query_radius(
            route_points_rad,
            r=SEARCH_RADIUS_RADIANS,
            return_distance=False,
        )

        # ─────────────────────────────────────────────────────────────
        # [Bug2 修正] sklearn BallTree.query_radius 在感測器稀疏區域會
        # 回傳大量空 ndarray，直接 np.concatenate 會 ValueError。
        # 改為先過濾空陣列再合併，並明確指定 dtype=int 避免型別歧義。
        # ─────────────────────────────────────────────────────────────
        non_empty_indices = [idx for idx in indices_list if idx.size > 0]
        if non_empty_indices:
            unique_indices = np.unique(np.concatenate(non_empty_indices))
            unique_indices = unique_indices[unique_indices < len(sensor_pm25_values)]
        else:
            unique_indices = np.array([], dtype=int)

        # [修正⑥] 加入 data_coverage 欄位，讓前端能區分「無資料路線」與「真實低污染路線」
        if unique_indices.size > 0:
            valid_pm25 = sensor_pm25_values[unique_indices]
            avg_exposure = float(np.mean(valid_pm25))
            max_exposure = float(np.max(valid_pm25))
            # 以感測器覆蓋率衡量資料品質（有感測器的節點 / 總節點數）
            covered_points = sum(1 for idx_arr in indices_list if idx_arr.size > 0)
            coverage_ratio = covered_points / len(route.coordinates)
            if coverage_ratio >= 0.8:
                data_coverage = "full"
            elif coverage_ratio >= 0.3:
                data_coverage = "partial"
            else:
                data_coverage = "sparse"
        else:
            # 無感測器時以全域均值兜底，並明確標記
            avg_exposure = float(df_sensors["pm25"].mean())
            max_exposure = float(df_sensors["pm25"].max())
            data_coverage = "none"

        evaluated_results.append({
            "route_id": route.route_id,
            "average_exposure_pm25": round(avg_exposure, 2),
            "peak_exposure_pm25": round(max_exposure, 2),
            "analyzed_points_count": len(route.coordinates),
            "matched_sensors_count": int(unique_indices.size),
            "data_coverage": data_coverage,          # "full" | "partial" | "sparse" | "none"
        })

    # 依平均 PM2.5 由低到高排序
    evaluated_results.sort(key=lambda x: x["average_exposure_pm25"])

    # 動態標記推薦路線（僅在資料覆蓋率足夠時標記，避免誤導）
    for i, res in enumerate(evaluated_results):
        res["is_ai_recommended"] = (i == 0 and res["data_coverage"] != "none")

    return {
        "status": "success",
        "results": evaluated_results,
    }


if __name__ == "__main__":
    import uvicorn
    # NOTE: 請勿使用 --workers N (N>1)，本地快取為 process 獨立狀態。
    #       若需多 worker，請移除 _local_cache 並完全依賴 Redis。
    uvicorn.run("searchpath:app", host="0.0.0.0", port=8000, reload=True)