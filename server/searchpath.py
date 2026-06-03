import asyncio
import io
import json
import math
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
# [修正] follow_redirects=True：Google Sheets export 會先回 307 跳轉至
#        googleusercontent.com，httpx 預設不跟隨重定向，加此參數後才能
#        正確抓到 CSV 內容。
http_client = httpx.AsyncClient(timeout=10.0, follow_redirects=True)

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

# ── 智慧路徑規劃新增常數 ──────────────────────────────────────────
# 各交通方式行駛速度（km/h），用於計算暴露時間
MODE_SPEED_KMH: dict = {"driving": 40, "cycling": 15, "walking": 5}

# 沿路熱區偵測半徑（公尺）→ 轉為弧度供 BallTree 使用
HOTSPOT_SEARCH_M   = 500
HOTSPOT_RADIUS_RAD = HOTSPOT_SEARCH_M / 6_371_000

# 超過此 PM2.5 閾值才觸發繞路邏輯
DETOUR_THRESHOLD_PM25 = 35.5

# 繞路偏移量（公尺）★ 400 → 800：確保 Waypoint 能真正逃出污染熱區
DETOUR_OFFSET_M = 800

# ── 智慧路徑「評分」專用半徑 ─────────────────────────────────────
# 全域半徑 SEARCH_RADIUS_RADIANS = 6.4 km 涵蓋全城感測器，
# 導致「繞開污染」與「穿越污染」的分數幾乎相同，系統無法辨別。
# 智慧路徑改用 400 m 小半徑：只計算「這條街本身」的感測器讀值，
# 走污染街道 vs 繞開後替代道路的分數才會真正不同。
SMART_SCORE_M          = 400
SMART_SCORE_RADIUS_RAD = SMART_SCORE_M / 6_371_000

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

# ── 智慧路徑規劃 Request Model ────────────────────────────────────
class SmartRouteRequest(BaseModel):
    start: Coordinate
    end:   Coordinate
    mode:  str = "driving"

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        if v not in ("driving", "cycling", "walking"):
            raise ValueError("mode 必須為 driving、cycling 或 walking")
        return v

# ── 熱區偵測 Request / Response ───────────────────────────────────
class DetectHotspotsRequest(BaseModel):
    """前端送來的基礎路線座標（已由前端呼叫 OSRM 取得）"""
    mode:        str            = "driving"
    coordinates: List[Coordinate]           # 主路線座標陣列

    @field_validator("coordinates")
    @classmethod
    def must_have_coords(cls, v):
        if len(v) < 2:
            raise ValueError("座標陣列至少需要 2 個點")
        return v

# ── 多路線評分 Request ────────────────────────────────────────────
class ScoreRoutesRequest(BaseModel):
    mode:   str = "driving"
    routes: List[RouteGeometry]   # 複數條路線（基礎 + 繞路）

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        if v not in ("driving", "cycling", "walking"):
            raise ValueError("mode 必須為 driving、cycling 或 walking")
        return v

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

        # ── 路線總長度（Haversine 向量化計算）────────────────────
        # 使用 np.diff 對相鄰座標逐段計算弧長後加總，
        # 結果作為同 PM2.5 時的次要排序依據（短路線優先）。
        if len(route_points) >= 2:
            lat_r = route_points_rad[:, 0]
            lon_r = route_points_rad[:, 1]
            dlat  = np.diff(lat_r)
            dlon  = np.diff(lon_r)
            a_seg = (np.sin(dlat / 2) ** 2
                     + np.cos(lat_r[:-1]) * np.cos(lat_r[1:]) * np.sin(dlon / 2) ** 2)
            total_distance_m = float(np.sum(2 * 6_371_000 * np.arcsin(np.sqrt(np.clip(a_seg, 0, 1)))))
        else:
            total_distance_m = 0.0

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
            "route_id":              route.route_id,
            "average_exposure_pm25": round(avg_exposure, 2),
            "peak_exposure_pm25":    round(max_exposure, 2),
            "analyzed_points_count": len(route.coordinates),
            "matched_sensors_count": int(unique_indices.size),
            "total_distance_m":      round(total_distance_m),   # 新增：路線總長（公尺）
            "data_coverage":         data_coverage,
        })

    # ──────────────────────────────────────────────────────────────────
    # 多準則排序（修正：純 PM2.5 排序導致等值時優先選較長路線的問題）
    #
    # 邏輯：
    #   ① 主要條件：平均 PM2.5（取整數 bin，容忍 <1 µg/m³ 的微小差異）
    #      → 相差不到 1 µg/m³ 的路線視為「空氣品質相同」
    #   ② 次要條件：路線總長度（同 PM2.5 等級時，短路線優先）
    #
    # 範例：
    #   Route A 39.29 µg/m³  1500 m  → bin=39, 1500m
    #   Route B 39.29 µg/m³  1100 m  → bin=39, 1100m → 排序第一 ✓
    #   Route C 38.50 µg/m³  2000 m  → bin=38, 2000m → 排序第一（PM2.5 明確更低）✓
    # ──────────────────────────────────────────────────────────────────
    evaluated_results.sort(key=lambda x: (
        int(x["average_exposure_pm25"]),   # PM2.5 整數 bin（主）
        x["total_distance_m"]              # 路線距離（次，短優先）
    ))

    # 動態標記推薦路線（僅在資料覆蓋率足夠時標記，避免誤導）
    for i, res in enumerate(evaluated_results):
        res["is_ai_recommended"] = (i == 0 and res["data_coverage"] != "none")

    return {
        "status": "success",
        "results": evaluated_results,
    }




# ═══════════════════════════════════════════════════════════════════
# 智慧路徑規劃 — 輔助函式群
# ═══════════════════════════════════════════════════════════════════

def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """純量 Haversine：回傳兩點間距離（公尺）"""
    R = 6_371_000
    f1, f2 = math.radians(lat1), math.radians(lat2)
    df = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a  = math.sin(df / 2) ** 2 + math.cos(f1) * math.cos(f2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(max(0.0, min(1.0, a))))


async def _call_osrm(
    start: Coordinate,
    end: Coordinate,
    mode: str,
    waypoints: Optional[list] = None,
) -> list:
    """
    呼叫公共 OSRM 伺服器取得路線。
    waypoints: [(lat, lon), ...] 中間必經點清單（可選）。
    """
    if waypoints:
        parts = [f"{start.lon},{start.lat}"]
        for wp_lat, wp_lon in waypoints:
            parts.append(f"{wp_lon},{wp_lat}")
        parts.append(f"{end.lon},{end.lat}")
        coords_str  = ";".join(parts)
        alternatives = "false"
    else:
        coords_str   = f"{start.lon},{start.lat};{end.lon},{end.lat}"
        alternatives = "true"

    url = (
        f"https://router.project-osrm.org/route/v1/{mode}/{coords_str}"
        f"?geometries=geojson&alternatives={alternatives}&overview=full"
    )
    try:
        resp = await http_client.get(url, timeout=8.0)
        data = resp.json()
        if data.get("code") == "Ok":
            return data.get("routes", [])
    except Exception as exc:
        print(f"OSRM 呼叫失敗 ({mode}, wp={waypoints}): {exc}")
    return []


def _find_hotspots(
    route_latlon: list,
    df: "pd.DataFrame",
    tree: "BallTree",
    threshold: float = DETOUR_THRESHOLD_PM25,
    max_spots: int = 2,
) -> list:
    """
    沿路線（500 m 搜尋半徑）找出 PM2.5 超標熱區，
    回傳 [(lat, lon, pm25), ...] 降序排列，且彼此間距 >= 300 m。
    """
    if len(route_latlon) < 2:
        return []

    pm25_col = df["pm25"].values
    step     = max(1, len(route_latlon) // 60)
    samples  = route_latlon[::step]
    srad     = np.radians(np.array(samples))

    indices_list = tree.query_radius(srad, r=HOTSPOT_RADIUS_RAD, return_distance=False)

    candidates: list = []
    for (lat, lon), idx_arr in zip(samples, indices_list):
        if idx_arr.size == 0:
            continue
        above = pm25_col[idx_arr]
        above = above[above >= threshold]
        if above.size == 0:
            continue
        candidates.append((lat, lon, float(np.max(above))))

    candidates.sort(key=lambda x: -x[2])
    hotspots: list = []
    for cand in candidates:
        lat, lon, pm = cand
        if all(_haversine_m(lat, lon, h[0], h[1]) >= 300 for h in hotspots):
            hotspots.append(cand)
        if len(hotspots) >= max_spots:
            break
    return hotspots


def _make_detour_waypoint(
    route_latlon: list,
    cluster_lat: float,
    cluster_lon: float,
    offset_m: float,
    side: int,
) -> Optional[tuple]:
    """
    在路線最近路段上，往垂直方向偏移 offset_m，產生繞路 Waypoint。
    side=+1 左偏，side=-1 右偏。
    """
    if len(route_latlon) < 2:
        return None

    min_d, closest = float("inf"), 0
    for i, (lat, lon) in enumerate(route_latlon):
        d = _haversine_m(lat, lon, cluster_lat, cluster_lon)
        if d < min_d:
            min_d, closest = d, i

    if min_d > 1500:
        return None

    i   = max(1, min(closest, len(route_latlon) - 2))
    la0, lo0 = route_latlon[i - 1]
    la1, lo1 = route_latlon[min(i + 1, len(route_latlon) - 1)]
    dlat = la1 - la0;  dlon = lo1 - lo0
    length = math.sqrt(dlat ** 2 + dlon ** 2)
    if length < 1e-10:
        return None
    dlat /= length;  dlon /= length

    perp_lat = -dlon * side;  perp_lon = dlat * side
    mid_lat  = (la0 + la1) / 2;  mid_lon = (lo0 + lo1) / 2
    d_lat = offset_m / 111_000
    d_lon = offset_m / (111_000 * math.cos(math.radians(mid_lat)))

    return (
        round(mid_lat + perp_lat * d_lat, 6),
        round(mid_lon + perp_lon * d_lon, 6),
    )


def _score_route(
    osrm_route: dict,
    route_id: str,
    strategy: str,
    df: "pd.DataFrame",
    tree: "BallTree",
    mode: str,
    score_radius_rad: float = SMART_SCORE_RADIUS_RAD,  # ★ 預設用街道級 400m 半徑
) -> dict:
    """
    多準則評分。
    暴露指數 = avg_PM2.5 × 行駛時間(秒)
    行駛時間由路線距離 / 交通方式速度決定，步行暴露量最高。
    """
    geom  = osrm_route["geometry"]["coordinates"]   # [lon, lat]
    dist_m = osrm_route.get("distance", 0)
    latlon = [[c[1], c[0]] for c in geom]

    speed_ms  = MODE_SPEED_KMH.get(mode, 30) * 1000 / 3600
    travel_s  = dist_m / speed_ms if speed_ms > 0 else 0

    # 向量化計算路線總長
    pts_rad = np.radians(np.array(latlon))
    if len(pts_rad) >= 2:
        dlat  = np.diff(pts_rad[:, 0]);  dlon = np.diff(pts_rad[:, 1])
        a_seg = (
            np.sin(dlat / 2) ** 2
            + np.cos(pts_rad[:-1, 0]) * np.cos(pts_rad[1:, 0]) * np.sin(dlon / 2) ** 2
        )
        total_m = float(np.sum(2 * 6_371_000 * np.arcsin(np.sqrt(np.clip(a_seg, 0, 1)))))
    else:
        total_m = dist_m

    # PM2.5 空間查詢（沿路取樣最多 80 點）
    step       = max(1, len(pts_rad) // 80)
    sample_rad = pts_rad[::step]
    idx_list   = tree.query_radius(sample_rad, r=score_radius_rad, return_distance=False)
    non_empty  = [idx for idx in idx_list if idx.size > 0]
    pm25_col   = df["pm25"].values

    if non_empty:
        uid      = np.unique(np.concatenate(non_empty))
        uid      = uid[uid < len(pm25_col)]
        pm_vals  = pm25_col[uid]
        avg_pm25 = float(np.mean(pm_vals))
        max_pm25 = float(np.max(pm_vals))
        cov      = sum(1 for idx in idx_list if idx.size > 0) / len(sample_rad)
        data_cov = "full" if cov >= 0.8 else ("partial" if cov >= 0.3 else "sparse")
    else:
        avg_pm25 = float(df["pm25"].mean())
        max_pm25 = float(df["pm25"].max())
        data_cov = "none"

    exposure_index = avg_pm25 * travel_s

    return {
        "route_id":              route_id,
        "strategy":              strategy,
        "coordinates":           latlon,
        "average_exposure_pm25": round(avg_pm25, 2),
        "peak_exposure_pm25":    round(max_pm25, 2),
        "exposure_index":        round(exposure_index, 1),
        "travel_time_minutes":   round(travel_s / 60, 1),
        "distance_km":           round(total_m / 1000, 2),
        "analyzed_points_count": len(latlon),
        "data_coverage":         data_cov,
    }


# ═══════════════════════════════════════════════════════════════════
# /api/smart-route  端點
# ═══════════════════════════════════════════════════════════════════
@app.post("/api/smart-route")
@limiter.limit("10/minute")
async def smart_route(request: Request, payload: SmartRouteRequest):
    """
    Google Maps 風格的空污最佳化路徑規劃。

    流程：
      ① OSRM 取得基礎路線（含替代路線）
      ② 沿主路線偵測高污染熱區（PM2.5 >= 35.5）
      ③ 對每個熱區產生左右各一個繞路 Waypoint
      ④ 並發呼叫 OSRM 取得繞路路線
      ⑤ 所有候選以「暴露指數 = avg_PM2.5 × 行駛時間」評分
      ⑥ 去重後回傳前三名，標記推薦路線

    速度假設：駕車 40 km/h, 單車 15 km/h, 步行 5 km/h
    """
    df, tree = await fetch_sensor_data_and_build_tree()
    mode = payload.mode
    start = payload.start
    end   = payload.end

    # ① 基礎路線
    base_routes = await _call_osrm(start, end, mode)
    if not base_routes:
        raise HTTPException(status_code=502, detail="無法從 OSRM 取得導航路線，請稍後再試")

    candidates: list = []
    for i, r in enumerate(base_routes[:2]):
        candidates.append((r, "direct" if i == 0 else "direct_alt"))

    # ② 偵測高污染熱區
    main_latlon = [[c[1], c[0]] for c in base_routes[0]["geometry"]["coordinates"]]
    hotspots    = _find_hotspots(main_latlon, df, tree)

    # ③ 產生繞路 Waypoint
    detour_tasks: list = []
    for c_lat, c_lon, _pm in hotspots:
        for side, label in [(1, "detour_clean_A"), (-1, "detour_clean_B")]:
            wp = _make_detour_waypoint(main_latlon, c_lat, c_lon, DETOUR_OFFSET_M, side)
            if wp:
                detour_tasks.append((wp, label))

    # ④ 並發 OSRM 呼叫
    if detour_tasks:
        results = await asyncio.gather(
            *[_call_osrm(start, end, mode, waypoints=[wp]) for wp, _ in detour_tasks],
            return_exceptions=True,
        )
        for (_, label), res in zip(detour_tasks, results):
            if isinstance(res, Exception) or not res:
                continue
            candidates.append((res[0], label))

    # ⑤ 評分 + 去重（距離差 < 50 m 視為同一路線）
    # ★ 使用 SMART_SCORE_RADIUS_RAD（400 m）而非全域 6.4 km，
    #   確保「走污染街道」vs「繞開後的替代道路」能得到不同分數
    scored: list = []
    seen_bins: set = set()
    for i, (osrm_r, strategy) in enumerate(candidates):
        bin_ = round(osrm_r.get("distance", 0) / 50)
        if bin_ in seen_bins:
            continue
        seen_bins.add(bin_)
        scored.append(
            _score_route(osrm_r, f"route_{i}", strategy, df, tree, mode,
                         score_radius_rad=SMART_SCORE_RADIUS_RAD)
        )

    if not scored:
        raise HTTPException(status_code=500, detail="無法評估任何路線")

    # ⑥ 排序：平均 PM2.5（主）→ 距離（次）
    # ★ 推薦邏輯：污染濃度最低的路線，不論行駛時間
    #
    # 為什麼不用暴露指數（PM2.5 × 時間）：
    #   較快的路線即使 PM2.5 更高，因行駛時間短，暴露指數反而較小，
    #   導致系統推薦「最快」而非「最乾淨」的路線，與使用者直覺相悖。
    #
    # 排序規則：
    #   ① PM2.5 整數 bin（每 1 µg/m³ 一格，相差 < 1 µg/m³ 視為同等）
    #   ② 同等污染時，距離較短的路線優先（節省時間）
    scored.sort(key=lambda x: (
        x["average_exposure_pm25"],   # 平均 PM2.5 直接比較（主）→ 污染最低優先
        x["distance_km"],             # 距離（次，同 PM2.5 時短路線優先）
    ))

    for i, res in enumerate(scored[:3]):
        res["is_ai_recommended"] = (i == 0 and res["data_coverage"] != "none")

    # ── debug 欄位：前端可用來確認熱區偵測是否正常運作 ──────────────
    debug_info = {
        "score_radius_m":     SMART_SCORE_M,
        "detour_offset_m":    DETOUR_OFFSET_M,
        "hotspots_detected":  len(hotspots),
        "hotspot_details": [
            {"lat": round(h[0], 5), "lon": round(h[1], 5), "pm25": round(h[2], 1)}
            for h in hotspots
        ],
        "detours_attempted":  len(detour_tasks),
        "candidates_before_dedup": len(candidates),
        "candidates_after_dedup":  len(scored),
    }

    return {
        "status": "success",
        "mode":   mode,
        "debug":  debug_info,
        "results": scored[:3],
    }



# ═══════════════════════════════════════════════════════════════════
# _snap_to_road：把偏移 Waypoint 吸附到最近的實際道路節點
# 解決偏移點落在山區/公園造成 OSRM 繞遠路的問題
# ═══════════════════════════════════════════════════════════════════
async def _snap_to_road(lat: float, lon: float, mode: str = "driving") -> Optional[tuple]:
    """
    呼叫 OSRM /nearest，把任意座標吸附到最近的可行道路節點。

    規則：
    - 若最近道路 ≤ 300m → 回傳吸附後的座標
    - 若最近道路 > 300m → 回傳 None（附近沒路，放棄此 Waypoint）

    這樣可確保繞路 Waypoint 一定落在真實道路上，
    OSRM 就不需要繞遠路去接一個在山上的點。
    """
    url = (
        f"https://router.project-osrm.org/nearest/v1/{mode}"
        f"/{lon},{lat}?number=1"
    )
    try:
        resp = await http_client.get(url, timeout=5.0)
        data = resp.json()
        if data.get("code") == "Ok" and data.get("waypoints"):
            wp   = data["waypoints"][0]
            dist = wp.get("distance", 9999)
            if dist <= 300:   # 300m 以內才算有效道路節點
                snapped_lon, snapped_lat = wp["location"]
                return (round(snapped_lat, 6), round(snapped_lon, 6))
            else:
                print(f"  _snap_to_road: ({lat},{lon}) 最近道路 {dist:.0f}m > 300m，放棄此 Waypoint")
    except Exception as e:
        print(f"  _snap_to_road 失敗 ({lat},{lon}): {e}")
    return None

# ═══════════════════════════════════════════════════════════════════
# /api/detect-hotspots  ── 步驟①後端
# 接收前端已取得的路線座標，回傳高污染熱區與繞路 waypoints
# 前端用 waypoints 再呼叫 OSRM 取得繞路路線
# ═══════════════════════════════════════════════════════════════════
@app.post("/api/detect-hotspots")
@limiter.limit("20/minute")
async def detect_hotspots(request: Request, payload: DetectHotspotsRequest):
    """
    接收主路線座標 → 偵測 PM2.5 熱區 → 回傳繞路 waypoints。
    前端拿到 waypoints 後自行呼叫 OSRM 取得繞路路線，
    再把所有路線送給 /api/score-routes 統一評分。
    """
    df, tree = await fetch_sensor_data_and_build_tree()

    route_latlon = [[c.lat, c.lon] for c in payload.coordinates]
    hotspots     = _find_hotspots(route_latlon, df, tree)

    # ① 產生原始偏移 Waypoints
    raw_waypoints = []
    for c_lat, c_lon, pm_val in hotspots:
        for side, label in [(1, "detour_clean_A"), (-1, "detour_clean_B")]:
            wp = _make_detour_waypoint(
                route_latlon, c_lat, c_lon, DETOUR_OFFSET_M, side
            )
            if wp:
                raw_waypoints.append({
                    "lat":       wp[0],
                    "lon":       wp[1],
                    "label":     label,
                    "near_pm25": round(pm_val, 1),
                })

    # ② 並發呼叫 OSRM /nearest，把偏移點吸附到最近的真實道路
    # 若偏移點落在山區/公園（最近道路 > 300m），直接捨棄該 Waypoint
    snap_results = await asyncio.gather(*[
        _snap_to_road(w["lat"], w["lon"], payload.mode)
        for w in raw_waypoints
    ], return_exceptions=True)

    waypoints = []
    for raw_wp, snapped in zip(raw_waypoints, snap_results):
        if isinstance(snapped, Exception) or snapped is None:
            print(f"  Waypoint ({raw_wp['lat']},{raw_wp['lon']}) 無法吸附到道路，已捨棄")
            continue
        waypoints.append({
            "lat":       snapped[0],
            "lon":       snapped[1],
            "label":     raw_wp["label"],
            "near_pm25": raw_wp["near_pm25"],
        })

    print(f"  detect_hotspots: {len(raw_waypoints)} 個原始 Waypoint → {len(waypoints)} 個有效道路節點")

    return {
        "status":              "success",
        "hotspots_detected":   len(hotspots),
        "hotspot_details": [
            {"lat": round(h[0], 5), "lon": round(h[1], 5), "pm25": round(h[2], 1)}
            for h in hotspots
        ],
        "waypoints": waypoints,
    }


# ═══════════════════════════════════════════════════════════════════
# /api/score-routes  ── 步驟②後端
# 接收所有路線座標（基礎 + 繞路），統一評分並回傳 Top 3
# ═══════════════════════════════════════════════════════════════════
@app.post("/api/score-routes")
@limiter.limit("10/minute")
async def score_routes(request: Request, payload: ScoreRoutesRequest):
    """
    接收多條路線座標（前端已呼叫 OSRM 取得幾何），
    以街道級 400 m 半徑評分，按 PM2.5 由低到高排序，回傳 Top 3。
    """
    if not payload.routes:
        raise HTTPException(status_code=400, detail="請提供至少一條路線")

    df, tree = await fetch_sensor_data_and_build_tree()
    mode     = payload.mode
    speed_ms = MODE_SPEED_KMH.get(mode, 30) * 1000 / 3600
    pm25_col = df["pm25"].values
    scored   = []

    for route in payload.routes:
        latlon   = [[c.lat, c.lon] for c in route.coordinates]
        pts_rad  = np.radians(np.array(latlon))

        # 路線總長（向量化 Haversine）
        if len(pts_rad) >= 2:
            dlat  = np.diff(pts_rad[:, 0]); dlon = np.diff(pts_rad[:, 1])
            a_seg = (
                np.sin(dlat / 2) ** 2
                + np.cos(pts_rad[:-1, 0]) * np.cos(pts_rad[1:, 0]) * np.sin(dlon / 2) ** 2
            )
            total_m = float(np.sum(2 * 6_371_000 * np.arcsin(np.sqrt(np.clip(a_seg, 0, 1)))))
        else:
            total_m = 0.0

        travel_s = total_m / speed_ms if speed_ms > 0 else 0

        # 街道級評分（400 m 半徑）
        step       = max(1, len(pts_rad) // 80)
        sample_rad = pts_rad[::step]
        idx_list   = tree.query_radius(sample_rad, r=SMART_SCORE_RADIUS_RAD, return_distance=False)
        non_empty  = [idx for idx in idx_list if idx.size > 0]

        if non_empty:
            uid_raw = np.unique(np.concatenate(non_empty))
            uid_raw = uid_raw[uid_raw < len(pm25_col)]

            # ── [B] 空間去重：以 0.001°(≈111m) 格子聚合相近感測點 ──
            # 車子停在同一紅燈產生多筆資料，聚合後每格只算一個代表值，
            # 避免單一密集採樣點對整條路線分數過度加權。
            sensor_lats = df["latitude"].values
            sensor_lons = df["longitude"].values
            grid_vals: dict = {}
            for i in uid_raw:
                grid_key = (
                    round(float(sensor_lats[i]), 3),
                    round(float(sensor_lons[i]), 3)
                )
                if grid_key not in grid_vals:
                    grid_vals[grid_key] = []
                grid_vals[grid_key].append(float(pm25_col[i]))

            # 每格取平均，再彙整成最終評分用的值
            cell_avgs = np.array([np.mean(v) for v in grid_vals.values()])
            avg_pm25  = float(np.mean(cell_avgs))
            max_pm25  = float(np.max(cell_avgs))   # 各格均值的最大值（非原始峰值）
            # ── 覆蓋率改用格子數 ──
            covered   = sum(1 for idx in idx_list if idx.size > 0)
            cov       = covered / max(len(sample_rad), 1)
            data_cov  = "full" if cov >= 0.8 else ("partial" if cov >= 0.3 else "sparse")
        else:
            avg_pm25 = float(df["pm25"].mean())
            max_pm25 = float(df["pm25"].max())
            data_cov = "none"

        scored.append({
            "route_id":              route.route_id,
            "coordinates":           latlon,
            "average_exposure_pm25": round(avg_pm25, 2),
            "peak_exposure_pm25":    round(max_pm25, 2),
            "exposure_index":        round(avg_pm25 * travel_s, 1),
            "travel_time_minutes":   round(travel_s / 60, 1),
            "distance_km":           round(total_m / 1000, 2),
            "analyzed_points_count": len(latlon),
            "data_coverage":         data_cov,
        })

    if not scored:
        raise HTTPException(status_code=500, detail="無法評估任何路線")

    # 排序：平均 PM2.5（主）→ 距離（次）
    scored.sort(key=lambda x: (x["average_exposure_pm25"], x["distance_km"]))

    for i, res in enumerate(scored[:3]):
        res["is_ai_recommended"] = (i == 0 and res["data_coverage"] != "none")

    return {
        "status":  "success",
        "mode":    mode,
        "results": scored[:3],
    }


# ═══════════════════════════════════════════════════════════════════
# /api/town-geojson  ── 鄉鎮市區 GeoJSON Proxy
# 從 g0v GitHub Raw 取得縣市 GeoJSON，後端快取後回傳給前端
# 前端無法直接取得區級資料，改用此 proxy 繞過 CORS/403 限制
# ═══════════════════════════════════════════════════════════════════
_town_geojson_cache: dict = {"data": None, "updated_at": None}
_TOWN_GEOJSON_TTL = timedelta(hours=24)   # GeoJSON 每日刷新即可

@app.get("/api/town-geojson")
@limiter.limit("30/minute")
async def get_town_geojson(request: Request):
    """
    回傳台灣縣市 GeoJSON（前端用於點位→縣市反查）。
    快取 24 小時，大幅降低對 GitHub Raw 的請求次數。
    """
    now = datetime.now()
    if (
        _town_geojson_cache["data"] is not None
        and _town_geojson_cache["updated_at"] is not None
        and (now - _town_geojson_cache["updated_at"]) < _TOWN_GEOJSON_TTL
    ):
        from fastapi.responses import JSONResponse
        return JSONResponse(content=_town_geojson_cache["data"])

    GEOJSON_URL = (
        "https://raw.githubusercontent.com/g0v/twgeojson/master/json/twCounty2010.geo.json"
    )
    try:
        resp = await http_client.get(GEOJSON_URL, timeout=15.0)
        resp.raise_for_status()
        data = resp.json()
        _town_geojson_cache["data"]       = data
        _town_geojson_cache["updated_at"] = now
        from fastapi.responses import JSONResponse
        return JSONResponse(content=data)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"GeoJSON 來源無法存取：{str(e)}")


# ── 台灣鄉鎮市區邊界框查找表（lat_min, lat_max, lon_min, lon_max）──
# 直接嵌入，不依賴任何外部 API，查詢速度 O(N) 約 0.1ms
TW_DISTRICT_BBOX: list[tuple] = [
    ("士林區",25.070,25.145,121.450,121.560),("北投區",25.095,25.195,121.420,121.545),
    ("內湖區",25.050,25.115,121.545,121.660),("南港區",25.040,25.090,121.575,121.660),
    ("松山區",25.040,25.080,121.540,121.590),("信義區",25.010,25.060,121.540,121.590),
    ("大安區",25.010,25.060,121.515,121.565),("中山區",25.045,25.085,121.510,121.570),
    ("中正區",25.018,25.055,121.490,121.540),("萬華區",25.018,25.055,121.475,121.525),
    ("大同區",25.047,25.080,121.490,121.535),("文山區",24.965,25.020,121.535,121.605),
    ("板橋區",24.990,25.025,121.430,121.490),("三重區",25.050,25.085,121.465,121.510),
    ("中和區",24.985,25.030,121.475,121.530),("永和區",25.005,25.030,121.505,121.535),
    ("新莊區",25.020,25.085,121.390,121.480),("新店區",24.915,24.995,121.490,121.600),
    ("樹林區",24.950,25.005,121.395,121.455),("鶯歌區",24.935,24.980,121.330,121.410),
    ("三峽區",24.845,24.960,121.280,121.425),("淡水區",25.120,25.220,121.390,121.500),
    ("汐止區",25.055,25.115,121.605,121.705),("瑞芳區",25.065,25.150,121.765,121.870),
    ("土城區",24.950,24.995,121.415,121.475),("蘆洲區",25.070,25.110,121.450,121.495),
    ("五股區",25.060,25.115,121.400,121.480),("泰山區",25.025,25.070,121.395,121.455),
    ("林口區",25.060,25.125,121.300,121.420),("深坑區",24.975,25.020,121.575,121.645),
    ("石碇區",24.930,24.995,121.590,121.710),("坪林區",24.870,24.950,121.670,121.780),
    ("三芝區",25.130,25.235,121.430,121.545),("石門區",25.170,25.310,121.490,121.590),
    ("八里區",25.100,25.165,121.355,121.460),("平溪區",25.000,25.080,121.720,121.820),
    ("雙溪區",25.010,25.110,121.820,121.960),("貢寮區",25.005,25.100,121.900,122.020),
    ("金山區",25.175,25.330,121.555,121.680),("萬里區",25.130,25.255,121.640,121.780),
    ("烏來區",24.840,24.940,121.510,121.660),
    ("桃園區",24.960,25.020,121.260,121.370),("中壢區",24.920,24.980,121.190,121.280),
    ("平鎮區",24.895,24.945,121.195,121.280),("八德區",24.930,24.985,121.255,121.335),
    ("楊梅區",24.855,24.930,121.080,121.210),("蘆竹區",25.020,25.095,121.230,121.360),
    ("大溪區",24.840,24.935,121.270,121.430),("龍潭區",24.840,24.900,121.160,121.270),
    ("龜山區",24.975,25.055,121.310,121.420),("大園區",25.025,25.100,121.135,121.265),
    ("觀音區",25.000,25.075,121.010,121.175),("新屋區",24.920,25.000,121.015,121.150),
    ("復興區",24.640,24.870,121.200,121.490),
    ("竹北市",24.820,24.900,121.000,121.095),("竹東鎮",24.680,24.760,121.050,121.170),
    ("新埔鎮",24.790,24.870,121.040,121.150),("關西鎮",24.760,24.850,121.125,121.260),
    ("湖口鄉",24.870,24.940,120.970,121.080),("新豐鄉",24.880,24.950,120.925,121.020),
    ("芎林鄉",24.720,24.810,121.025,121.120),("橫山鄉",24.650,24.745,121.100,121.230),
    ("五峰鄉",24.500,24.670,121.025,121.230),("尖石鄉",24.540,24.750,121.190,121.490),
    ("峨眉鄉",24.620,24.710,121.015,121.110),("寶山鄉",24.680,24.790,120.985,121.075),
    ("北埔鄉",24.620,24.700,121.040,121.155),
    ("苗栗市",24.540,24.600,120.790,120.870),("頭份市",24.670,24.720,120.865,120.940),
    ("竹南鎮",24.670,24.730,120.840,120.920),("通霄鎮",24.420,24.500,120.630,120.740),
    ("苑裡鎮",24.350,24.450,120.590,120.700),
    ("豐原區",24.210,24.285,120.700,120.790),("潭子區",24.175,24.230,120.680,120.760),
    ("大雅區",24.200,24.285,120.580,120.685),("神岡區",24.225,24.310,120.620,120.720),
    ("大肚區",24.080,24.160,120.495,120.610),("沙鹿區",24.100,24.190,120.490,120.595),
    ("梧棲區",24.130,24.200,120.485,120.580),("清水區",24.170,24.280,120.490,120.620),
    ("大甲區",24.280,24.380,120.540,120.655),("外埔區",24.270,24.360,120.590,120.705),
    ("后里區",24.280,24.370,120.665,120.800),("東勢區",24.220,24.330,120.780,120.950),
    ("新社區",24.180,24.290,120.780,120.940),("石岡區",24.235,24.295,120.760,120.860),
    ("和平區",24.070,24.380,120.820,121.260),
    ("西屯區",24.130,24.220,120.560,120.665),("南屯區",24.080,24.150,120.595,120.680),
    ("太平區",24.090,24.195,120.690,120.820),("大里區",24.065,24.140,120.640,120.720),
    ("霧峰區",24.000,24.095,120.630,120.770),("烏日區",24.060,24.125,120.555,120.660),
    ("北屯區",24.145,24.250,120.665,120.790),
    ("彰化市",24.060,24.110,120.505,120.565),("員林市",23.940,23.990,120.540,120.620),
    ("和美鎮",24.070,24.130,120.460,120.535),("鹿港鎮",24.040,24.100,120.390,120.470),
    ("南投市",23.890,23.945,120.630,120.720),("埔里鎮",23.940,24.010,120.930,121.020),
    ("草屯鎮",23.950,24.020,120.630,120.740),("竹山鎮",23.730,23.850,120.570,120.720),
    ("斗六市",23.680,23.745,120.440,120.535),("斗南鎮",23.640,23.700,120.420,120.520),
    ("虎尾鎮",23.695,23.755,120.395,120.465),("西螺鎮",23.775,23.830,120.445,120.530),
    ("屏東市",22.650,22.720,120.450,120.530),("潮州鎮",22.530,22.600,120.515,120.590),
    ("東港鎮",22.445,22.510,120.415,120.510),("恆春鎮",21.880,22.020,120.680,120.820),
    ("宜蘭市",24.730,24.780,121.720,121.780),("羅東鎮",24.670,24.720,121.750,121.820),
    ("蘇澳鎮",24.580,24.660,121.830,121.930),("頭城鎮",24.830,24.950,121.750,121.920),
    ("礁溪鄉",24.760,24.850,121.720,121.830),
    ("花蓮市",23.940,24.010,121.575,121.640),("吉安鄉",23.895,23.960,121.555,121.635),
    ("台東市",22.720,22.810,121.075,121.185),
    ("馬公市",23.540,23.600,119.530,119.640),("金城鎮",24.400,24.470,118.270,118.390),
    # 高雄市
    ("鹽埕區",22.620,22.640,120.270,120.300),("鼓山區",22.635,22.700,120.255,120.330),
    ("左營區",22.670,22.740,120.270,120.340),("楠梓區",22.720,22.815,120.285,120.390),
    ("三民區",22.620,22.685,120.295,120.370),("新興區",22.625,22.655,120.290,120.330),
    ("前金區",22.620,22.645,120.280,120.315),("苓雅區",22.610,22.645,120.305,120.355),
    ("前鎮區",22.575,22.640,120.285,120.360),("旗津區",22.540,22.635,120.245,120.290),
    ("小港區",22.540,22.615,120.300,120.390),("鳳山區",22.610,22.665,120.340,120.415),
    ("林園區",22.480,22.565,120.355,120.450),("大寮區",22.570,22.655,120.370,120.455),
    ("大樹區",22.650,22.755,120.390,120.500),("大社區",22.720,22.795,120.340,120.430),
    ("仁武區",22.680,22.755,120.325,120.415),("鳥松區",22.645,22.710,120.355,120.430),
    ("岡山區",22.785,22.875,120.265,120.375),("橋頭區",22.750,22.820,120.285,120.370),
    ("燕巢區",22.745,22.840,120.350,120.455),("田寮區",22.830,22.945,120.335,120.490),
    ("阿蓮區",22.845,22.930,120.235,120.355),("路竹區",22.845,22.920,120.220,120.320),
    ("湖內區",22.890,22.975,120.160,120.275),("茄萣區",22.910,22.990,120.115,120.230),
    ("永安區",22.820,22.910,120.100,120.210),("彌陀區",22.770,22.855,120.140,120.255),
    ("梓官區",22.740,22.815,120.195,120.295),("旗山區",22.855,22.990,120.430,120.570),
    ("美濃區",22.870,22.985,120.520,120.640),
    # 台南市
    ("永康區",23.015,23.095,120.210,120.320),("歸仁區",22.920,22.995,120.240,120.350),
    ("新化區",22.960,23.055,120.275,120.405),("安南區",23.000,23.100,120.095,120.260),
    ("安平區",22.980,23.015,120.135,120.195),("仁德區",22.945,22.990,120.190,120.295),
    ("麻豆區",23.065,23.150,120.210,120.355),("佳里區",23.115,23.195,120.115,120.235),
    ("新營區",23.270,23.370,120.265,120.390),("善化區",23.055,23.145,120.260,120.395),
    ("學甲區",23.185,23.280,120.105,120.245),("鹽水區",23.250,23.355,120.145,120.305),
    ("七股區",23.060,23.190,120.020,120.175),("官田區",23.075,23.145,120.320,120.445),
]

def lookup_district_bbox(lat: float, lon: float) -> str:
    """邊界框查找：O(N) 掃描，優先選最小面積的匹配區"""
    matches = []
    for entry in TW_DISTRICT_BBOX:
        name, lat_min, lat_max, lon_min, lon_max = entry
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            area = (lat_max - lat_min) * (lon_max - lon_min)
            matches.append((area, name))
    if not matches:
        return "其他"
    matches.sort()
    return matches[0][1]


# ── 批次座標→行政區查詢 Request Model ──────────────────────────
class BatchDistrictsRequest(BaseModel):
    coordinates: List[Coordinate]

    @field_validator("coordinates")
    @classmethod
    def limit_size(cls, v):
        if len(v) > 1000:
            raise ValueError("單次最多查詢 1000 個座標")
        return v


@app.post("/api/batch-districts")
@limiter.limit("30/minute")
async def batch_districts(request: Request, payload: BatchDistrictsRequest):
    """
    批次座標 → 行政區名稱查詢（純本地邊界框查找，無外部 API 依賴）。
    不依賴 NLSC / Nominatim 等任何外部服務，即時回傳，零延遲。
    邊界框精度約 ±500m，適合顯示用途。
    """
    results = {}
    for c in payload.coordinates:
        name = lookup_district_bbox(c.lat, c.lon)
        results[f"{c.lat},{c.lon}"] = name
    return {"results": results}


# ═══════════════════════════════════════════════════════════════════
# /api/weather  ── 天氣資料 Proxy（繞過前端 CORS 限制）
# ═══════════════════════════════════════════════════════════════════
@app.get("/api/weather")
@limiter.limit("30/minute")
async def get_weather(request: Request):
    """
    從 open-meteo 取得台北即時天氣，後端 proxy 給前端使用。
    open-meteo 在部分 CDN/邊緣節點沒有回傳 CORS header，
    由後端呼叫可完全繞開此限制。快取 10 分鐘。
    """
    # 簡單記憶體快取（10 分鐘），避免每個使用者都打一次 open-meteo
    _wc: dict = getattr(get_weather, "_cache", {})
    get_weather._cache = _wc
    now_ts = datetime.now().timestamp()
    if _wc.get("data") and now_ts - _wc.get("ts", 0) < 600:
        from fastapi.responses import JSONResponse
        return JSONResponse(content=_wc["data"])

    url = (
        "https://api.open-meteo.com/v1/forecast"
        "?latitude=25.0330&longitude=121.5654"
        "&current=temperature_2m,wind_speed_10m,weather_code"
        "&timezone=Asia%2FTaipei"
    )
    try:
        resp = await http_client.get(url, timeout=8.0)
        resp.raise_for_status()
        data = resp.json()
        _wc["data"] = data
        _wc["ts"]   = now_ts
        from fastapi.responses import JSONResponse
        return JSONResponse(content=data)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"天氣資料無法取得：{str(e)}")

if __name__ == "__main__":
    import uvicorn
    # NOTE: 請勿使用 --workers N (N>1)，本地快取為 process 獨立狀態。
    #       若需多 worker，請移除 _local_cache 並完全依賴 Redis。
    uvicorn.run("searchpath:app", host="0.0.0.0", port=8000, reload=True)