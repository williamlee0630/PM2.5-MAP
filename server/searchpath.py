# searchpath.py
# 空污共犯 - 健康路徑規劃子系統 API（優化版）

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
# CORS 設定（正式上線請將 * 改為你的前端網域）
# 範例：allow_origins=["https://your-frontend-domain.com"]
# ─────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],        # TODO: 上線前請限縮為指定網域
    allow_credentials=False,   # 修正：與 allow_origins=["*"] 同時為 True 會被瀏覽器拒絕
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

# 感測器搜尋半徑：0.01 弧度 ≈ 6.4 公里（haversine 度量單位為弧度）
SEARCH_RADIUS_RADIANS = 0.01

# 感測器資料快取有效期限
CACHE_TTL = timedelta(minutes=5)

# ─────────────────────────────────────────────
# Pydantic 資料型態定義
# ─────────────────────────────────────────────
class RouteGeometry(BaseModel):
    route_id: str                           # 路線識別碼
    coordinates: List[Tuple[float, float]]  # 該導航路線的經緯度點序列

class HealthRoutingRequest(BaseModel):
    routes: List[RouteGeometry]

# ─────────────────────────────────────────────
# 感測器資料快取（模組層級，跨請求共享）
# ─────────────────────────────────────────────
_cache: dict = {
    "df": None,
    "tree": None,
    "updated_at": None,
}

async def fetch_sensor_data_and_build_tree() -> Tuple[pd.DataFrame, BallTree]:
    """
    從 Google Sheets 即時抓取開源數據，並建立 BallTree 空間索引。
    
    優化重點：
    1. 使用 httpx 非同步請求，避免阻塞 FastAPI 事件迴圈
    2. 加入 TTL 快取機制，每 5 分鐘才重新抓取，減少外部請求次數
    3. 改用 BallTree + haversine，正確處理球面距離，避免經緯度失真
    """
    now = datetime.now()

    # 快取有效則直接回傳，不重複下載
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

        # 將座標轉換為弧度，BallTree haversine 距離單位為弧度
        coords_rad = np.radians(df[["latitude", "longitude"]].values)
        spatial_tree = BallTree(coords_rad, metric="haversine")

        # 更新快取
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
    """伺服器健康檢查（讓 Render 順利通過驗證）"""
    return {"status": "online", "message": "空污共犯 API 伺服器運作中！"}


@app.get("/api/geocode")
@limiter.limit("1/second")         # 遵守 Nominatim 使用政策：每秒最多 1 次請求
async def geocode_address(request: Request, q: str):
    """
    由 Python 代替前端向 Nominatim 發送請求，徹底繞過瀏覽器 CORS 限制。

    優化重點：
    1. 改為 async，使用 httpx 非同步請求
    2. 修正 response 變數在例外中可能未定義的問題
    3. 加入 slowapi 速率限制，防止 IP 被 Nominatim 封鎖
    """
    if not q or not q.strip():
        raise HTTPException(status_code=400, detail="請提供查詢地址")

    headers = {
        "User-Agent": "AirPollutionAccompliceProject/1.0 (contact@su.edu.tw)",
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    params = {
        "format": "json",
        "q": q.strip(),
        "countrycodes": "tw",
        "limit": 1,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                "https://nominatim.openstreetmap.org/search",
                headers=headers,
                params=params,
            )
            response.raise_for_status()

        data = response.json()

        if not data:
            raise HTTPException(status_code=404, detail=f"找不到該地址的地理座標：{q}")

        return {
            "lat": float(data[0]["lat"]),
            "lon": float(data[0]["lon"]),
        }

    except HTTPException:
        # 讓 FastAPI 的 HTTPException 正常往上傳遞，不被下方 except 攔截
        raise
    except httpx.HTTPStatusError as e:
        # 修正：直接從例外物件取得 status_code，不依賴外部 response 變數
        print(f"OSM 伺服器拒絕請求: 狀態碼 {e.response.status_code}")
        raise HTTPException(status_code=502, detail="地圖伺服器暫時阻擋了請求，請稍後再試")
    except httpx.TimeoutException:
        print("OSM 伺服器連線逾時")
        raise HTTPException(status_code=504, detail="第三方地圖伺服器連線逾時，請重新嘗試")
    except Exception as e:
        print(f"地理編碼發生未預期錯誤: {str(e)}")
        raise HTTPException(status_code=500, detail="第三方地理資訊服務暫時無回應")
    except httpx.HTTPStatusError as e:
    # 加這行，去 Render log 看實際狀態碼是幾號
    print(f"實際狀態碼: {e.response.status_code}, 回應內容: {e.response.text}")
    raise HTTPException(status_code=502, ...)


@app.post("/api/calculate-health-routes")
async def calculate_health_routes(payload: HealthRoutingRequest):
    """
    健康路徑權重運算端點。
    接收前端傳來的多條替代路線軌跡，利用 BallTree 進行精準空污曝露成本估算。

    優化重點：
    1. fetch_sensor_data_and_build_tree 改為 async，避免事件迴圈阻塞
    2. 搜尋距離改用弧度單位，搭配 BallTree haversine，消除經緯度失真
    """
    if not payload.routes:
        raise HTTPException(status_code=400, detail="請提供至少一條待評估路線")

    # 1. 取得最新感測資料與空間索引樹（有快取則直接使用）
    df_sensors, spatial_tree = await fetch_sensor_data_and_build_tree()
    sensor_pm25_values = df_sensors["pm25"].values

    evaluated_results = []

    # 2. 迭代評估每條替代路線
    for route in payload.routes:
        if not route.coordinates:
            continue

        route_points = np.array(route.coordinates)

        # 3. 轉換為弧度，查詢各點最近感測器（半徑 SEARCH_RADIUS_RADIANS ≈ 6.4 公里）
        route_points_rad = np.radians(route_points)
        indices_list, distances_list = spatial_tree.query_radius(
            route_points_rad,
            r=SEARCH_RADIUS_RADIANS,
            return_distance=True,
        )

        # 4. 收集範圍內所有有效 PM2.5 值
        valid_pm25_list = []
        for idxs in indices_list:
            for idx in idxs:
                if idx < len(sensor_pm25_values):
                    valid_pm25_list.append(sensor_pm25_values[idx])

        # 5. 計算整體曝露指標（無感測器時回退至全域背景值）
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

    # 6. 根據平均曝露值由低到高排序
    evaluated_results.sort(key=lambda x: x["average_exposure_pm25"])

    # 7. 動態注入決策標籤
    for i, res in enumerate(evaluated_results):
        res["is_ai_recommended"] = (i == 0)

    return {
        "status": "success",
        "results": evaluated_results,
    }


# ─────────────────────────────────────────────
# 本地開發啟動入口
# ─────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("searchpath:app", host="0.0.0.0", port=8000, reload=True)