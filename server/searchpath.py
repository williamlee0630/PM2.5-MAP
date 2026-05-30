import io
from datetime import datetime, timedelta
from typing import List, Tuple

import httpx
import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sklearn.neighbors import BallTree
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

# ─────────────────────────────────────────────
# 應用程式初始化
# ─────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="空污共犯 - 健康路徑規劃子系統 API")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ─────────────────────────────────────────────
# CORS 設定
# ─────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],       # TODO: 上線前請限縮為指定網域
    allow_credentials=False,   # 與 allow_origins=["*"] 並存時必須為 False
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────
# 常數定義
# ─────────────────────────────────────────────
CSV_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1jcRopeeqnT786iB9m6Jd8oQH34S2AUE9Sp5-4eCgwYQ/export?format=csv"
)

# 感測器搜尋半徑：0.01 弧度 ≈ 6.4 公里
SEARCH_RADIUS_RADIANS = 0.01
# 感測器資料快取有效期限
CACHE_TTL = timedelta(minutes=5)

# ─────────────────────────────────────────────
# Pydantic 資料型態定義
# ─────────────────────────────────────────────
class RouteGeometry(BaseModel):
    route_id: str
    coordinates: List[Tuple[float, float]]

class HealthRoutingRequest(BaseModel):
    routes: List[RouteGeometry]

# ─────────────────────────────────────────────
# 感測器資料快取
# ─────────────────────────────────────────────
_cache: dict = {
    "df": None,
    "tree": None,
    "updated_at": None,
}

# ─────────────────────────────────────────────
# 地理編碼備援函式 (加入 ArcGIS 強大地標解析)
# ─────────────────────────────────────────────
async def _try_arcgis(client: httpx.AsyncClient, q: str) -> dict | None:
    """第一線服務：ArcGIS（對於台灣口語化地標、學校、捷運站解析能力極佳）"""
    r = await client.get(
        "https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/findAddressCandidates",
        params={"f": "json", "singleLine": q, "maxLocations": 1},
        timeout=10.0,
    )
    r.raise_for_status()
    candidates = r.json().get("candidates", [])
    if not candidates:
        return None
    loc = candidates[0]["location"]
    return {"lat": loc["y"], "lon": loc["x"]}

async def _try_photon(client: httpx.AsyncClient, q: str) -> dict | None:
    """第二線服務：Photon（基於 OSM，對雲端伺服器 IP 限制較寬鬆）"""
    r = await client.get(
        "https://photon.komoot.io/api/",
        params={"q": q, "limit": 1, "lang": "zh"},
        timeout=10.0,
    )
    r.raise_for_status()
    features = r.json().get("features", [])
    if not features:
        return None
    coords = features[0]["geometry"]["coordinates"]
    return {"lat": coords[1], "lon": coords[0]}

async def _try_nominatim(client: httpx.AsyncClient, q: str) -> dict | None:
    """第三線服務：Nominatim"""
    r = await client.get(
        "https://nominatim.openstreetmap.org/search",
        headers={
            "User-Agent": "AirPollutionAccompliceProject/1.0 (contact@su.edu.tw)",
            "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        },
        params={"format": "json", "q": q, "countrycodes": "tw", "limit": 1},
        timeout=10.0,
    )
    r.raise_for_status()
    data = r.json()
    if not data:
        return None
    return {"lat": float(data[0]["lat"]), "lon": float(data[0]["lon"])}

# ─────────────────────────────────────────────
# 感測器資料抓取與快取
# ─────────────────────────────────────────────
async def fetch_sensor_data_and_build_tree() -> Tuple[pd.DataFrame, BallTree]:
    now = datetime.now()
    if (
        _cache["df"] is not None
        and _cache["updated_at"] is not None
        and (now - _cache["updated_at"]) < CACHE_TTL
    ):
        return _cache["df"], _cache["tree"]

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(CSV_URL)
            response.raise_for_status()

        df = pd.read_csv(io.StringIO(response.text))
        df = df.dropna(subset=["latitude", "longitude", "pm25"])

        if df.empty:
            raise ValueError("Google Sheets 內無有效感測數據")

        coords_rad = np.radians(df[["latitude", "longitude"]].values)
        spatial_tree = BallTree(coords_rad, metric="haversine")

        _cache["df"] = df
        _cache["tree"] = spatial_tree
        _cache["updated_at"] = now

        return df, spatial_tree

    except httpx.HTTPStatusError as e:
        print(f"Google Sheets 回應錯誤: {e.response.status_code}")
        raise HTTPException(status_code=502, detail="無法同步感測網路資料，來源伺服器拒絕請求")
    except httpx.TimeoutException:
        print("Google Sheets 連線逾時")
        raise HTTPException(status_code=504, detail="無法同步感測網路資料，連線逾時")
    except Exception as e:
        print(f"數據庫連線或建樹失敗: {str(e)}")
        raise HTTPException(status_code=500, detail="無法即時同步感測網路數據樞紐")

# ─────────────────────────────────────────────
# 路由端點
# ─────────────────────────────────────────────
@app.get("/")
def health_check():
    return {"status": "online", "message": "空污共犯 API 伺服器運作中！"}

@app.get("/api/geocode")
@limiter.limit("1/second")
async def geocode_address(request: Request, q: str):
    if not q or not q.strip():
        raise HTTPException(status_code=400, detail="請提供查詢地址")

    errors_count = 0
    # 依序嘗試：ArcGIS -> Photon -> Nominatim
    providers = [_try_arcgis, _try_photon, _try_nominatim]
    
    async with httpx.AsyncClient() as client:
        for provider in providers:
            try:
                result = await provider(client, q.strip())
                if result:
                    return result
                else:
                    # 服務正常回應，但找不到該地點 (回傳 None)
                    pass
            except Exception as e:
                print(f"{provider.__name__} 發生錯誤: {str(e)}")
                errors_count += 1
                continue

    # 判斷是「網路全掛」還是「真的找不到地點」
    if errors_count == len(providers):
        raise HTTPException(status_code=502, detail="所有地理編碼服務暫時無回應，請稍後再試")
    else:
        raise HTTPException(status_code=404, detail=f"地圖引擎找不到「{q}」，請嘗試輸入更完整的門牌地址或換個地標名稱")

@app.post("/api/calculate-health-routes")
async def calculate_health_routes(payload: HealthRoutingRequest):
    if not payload.routes:
        raise HTTPException(status_code=400, detail="請提供至少一條待評估路線")

    df_sensors, spatial_tree = await fetch_sensor_data_and_build_tree()
    sensor_pm25_values = df_sensors["pm25"].values

    evaluated_results = []

    for route in payload.routes:
        if not route.coordinates:
            continue

        route_points = np.array(route.coordinates)
        route_points_rad = np.radians(route_points)
        
        indices_list, distances_list = spatial_tree.query_radius(
            route_points_rad,
            r=SEARCH_RADIUS_RADIANS,
            return_distance=True,
        )

        valid_pm25_list = []
        for idxs in indices_list:
            for idx in idxs:
                if idx < len(sensor_pm25_values):
                    valid_pm25_list.append(sensor_pm25_values[idx])

        if valid_pm25_list:
            avg_exposure = float(np.mean(valid_pm25_list))
            max_exposure = float(np.max(valid_pm25_list))
        else:
            avg_exposure = float(df_sensors["pm25"].mean())
            max_exposure = float(df_sensors["pm25"].max())

        evaluated_results.append({
            "route_id": route.route_id,
            "average_exposure_pm25": round(avg_exposure, 2),
            "peak_exposure_pm25": round(max_exposure, 2),
            "analyzed_points_count": len(route.coordinates),
        })

    evaluated_results.sort(key=lambda x: x["average_exposure_pm25"])

    for i, res in enumerate(evaluated_results):
        res["is_ai_recommended"] = (i == 0)

    return {
        "status": "success",
        "results": evaluated_results,
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("searchpath:app", host="0.0.0.0", port=8000, reload=True)