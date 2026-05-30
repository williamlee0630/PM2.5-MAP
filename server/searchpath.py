import io
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import List, Tuple

import httpx
import numpy as np
import pandas as pd
import redis.asyncio as redis
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
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

SEARCH_RADIUS_RADIANS = 0.01
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
# 本地記憶體快取
# ─────────────────────────────────────────────
_local_cache: dict = {
    "df": None,
    "tree": None,
    "updated_at": None,
}

# ─────────────────────────────────────────────
# 感測器資料抓取與快取 (雙層快取架構)
# ─────────────────────────────────────────────
async def fetch_sensor_data_and_build_tree() -> Tuple[pd.DataFrame, BallTree]:
    now = datetime.now()
    
    if (
        _local_cache["df"] is not None
        and _local_cache["updated_at"] is not None
        and (now - _local_cache["updated_at"]) < CACHE_TTL
    ):
        return _local_cache["df"], _local_cache["tree"]

    csv_data = await redis_client.get("sensor_data_csv")
    
    if not csv_data:
        try:
            response = await http_client.get(CSV_URL)
            response.raise_for_status()
            csv_data = response.text
            await redis_client.set("sensor_data_csv", csv_data, ex=300)
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
    _local_cache["updated_at"] = now

    return df, spatial_tree

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

    # 1. 優先查詢 Redis 快取，避免浪費 Google API 額度
    cached_result = await redis_client.get(cache_key)
    if cached_result:
        return json.loads(cached_result)

    # 2. 確認是否設定 Google Maps API Key
    GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
    if not GOOGLE_MAPS_API_KEY:
        raise HTTPException(status_code=500, detail="伺服器尚未設定 Google Maps API 金鑰")

    # 3. 呼叫 Google Maps Geocoding API
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {
        "address": query,
        "key": GOOGLE_MAPS_API_KEY,
        "language": "zh-TW",
        "region": "tw"
    }

    try:
        response = await http_client.get(url, params=params)
        response.raise_for_status()
        data = response.json()

        if data.get("status") == "OK" and len(data.get("results", [])) > 0:
            location = data["results"][0]["geometry"]["location"]
            result = {"lat": location["lat"], "lon": location["lng"]}
            
            # 查詢成功，存入 Redis，設定 86400 秒 (24小時) 過期
            await redis_client.set(cache_key, json.dumps(result), ex=86400)
            return result
        else:
            raise HTTPException(status_code=404, detail=f"地圖引擎找不到「{query}」")

    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"連線至 Google 伺服器發生錯誤: {str(e)}")

@app.post("/api/calculate-health-routes")
async def calculate_health_routes(payload: HealthRoutingRequest):
    if not payload.routes:
        raise HTTPException(status_code=400, detail="請提供至少一條待評估路線")

    df_sensors, spatial_tree = await fetch_sensor_data_and_build_tree()
    sensor_pm25_values = df_sensors["pm25"].values

    evaluated_results = []

    for route in payload.routes:
        if not route.coordinates: continue

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