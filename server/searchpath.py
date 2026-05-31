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

    waypoints = []
    for c_lat, c_lon, pm_val in hotspots:
        for side, label in [(1, "detour_clean_A"), (-1, "detour_clean_B")]:
            wp = _make_detour_waypoint(
                route_latlon, c_lat, c_lon, DETOUR_OFFSET_M, side
            )
            if wp:
                waypoints.append({
                    "lat":   wp[0],
                    "lon":   wp[1],
                    "label": label,
                    "near_pm25": round(pm_val, 1),
                })

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
            uid      = np.unique(np.concatenate(non_empty))
            uid      = uid[uid < len(pm25_col)]
            pm_vals  = pm25_col[uid]
            avg_pm25 = float(np.mean(pm_vals))
            max_pm25 = float(np.max(pm_vals))
            cov      = sum(1 for idx in idx_list if idx.size > 0) / max(len(sample_rad), 1)
            data_cov = "full" if cov >= 0.8 else ("partial" if cov >= 0.3 else "sparse")
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

if __name__ == "__main__":
    import uvicorn
    # NOTE: 請勿使用 --workers N (N>1)，本地快取為 process 獨立狀態。
    #       若需多 worker，請移除 _local_cache 並完全依賴 Redis。
    uvicorn.run("searchpath:app", host="0.0.0.0", port=8000, reload=True)