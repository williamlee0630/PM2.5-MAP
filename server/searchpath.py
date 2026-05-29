# main.py
import requests
import io
import pandas as pd
import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Tuple
from scipy.spatial import cKDTree

app = FastAPI(title="空污共犯 - 健康路徑規劃子系統 API")

# 允許前端跨網域 4G/LTE 連線 (CORS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CSV_URL = "https://docs.google.com/spreadsheets/d/1jcRopeeqnT786iB9m6Jd8oQH34S2AUE9Sp5-4eCgwYQ/export?format=csv"

# --- Pydantic 資料型態定義 ---
class RouteGeometry(BaseModel):
    route_id: str                      # 路線識別碼 (例如: "route_fastest", "route_alternative")
    coordinates: List[Tuple[float, float]] # 該導航路線的經緯度點序列: [(lat1, lon1), (lat2, lon2), ...]

class HealthRoutingRequest(BaseModel):
    routes: List[RouteGeometry]

# --- 核心運算邏輯 ---
def fetch_sensor_data_and_build_tree() -> Tuple[pd.DataFrame, cKDTree]:
    """從 Google Sheets 即時抓取開源數據，並建立 K-D Tree 空間索引"""
    try:
        response = requests.get(CSV_URL, timeout=5)
        response.raise_for_status()
        
        # 讀取並清理資料
        df = pd.read_csv(io.StringIO(response.text))
        df = df.dropna(subset=['latitude', 'longitude', 'pm25'])
        
        if df.empty:
            raise ValueError("Google Sheets 內無有效感測數據")
            
        # 提取經緯度矩陣，建立 2D 空間查找樹
        sensor_coords = df[['latitude', 'longitude']].values
        spatial_tree = cKDTree(sensor_coords)
        
        return df, spatial_tree
    except Exception as e:
        print(f"數據庫連線或建樹失敗: {str(e)}")
        raise HTTPException(status_code=500, detail="無法即時同步感測網路數據樞紐")

@app.post("/api/calculate-health-routes")
async def calculate_health_routes(payload: HealthRoutingRequest):
    """
    健康路徑權重運算端點 (Endpoint)
    接收前端傳來的多條替代路線軌跡，利用 K-D Tree 進行精準空污曝露成本估算
    """
    if not payload.routes:
        raise HTTPException(status_code=400, detail="請提供至少一條待評估路線")
        
    # 1. 取得最新感測資料與空間索引樹
    df_sensors, spatial_tree = fetch_sensor_data_and_build_tree()
    sensor_pm25_values = df_sensors['pm25'].values
    
    evaluated_results = []
    
    # 2. 迭代評估每條替代路線
    for route in payload.routes:
        route_points = np.array(route.coordinates) # 轉換為 NumPy 陣列加速運算
        
        if len(route_points) == 0:
            continue
            
        # 3. 秒級高性能尋找：直接找出地圖路徑上各個點「最近的微型感測器」索引
        # distance_upper_bound=0.01 大約限制在方圓 1 公里內，避免拉到過遠的測站
        distances, indices = spatial_tree.query(route_points, k=1, distance_upper_bound=0.01)
        
        valid_pm25_list = []
        for dist, idx in zip(distances, indices):
            # 如果在限定距離內找到了最近的感測器點位
            if dist != np.inf and idx < len(sensor_pm25_values):
                valid_pm25_list.append(sensor_pm25_values[idx])
        
        # 4. 計算這條路線的整體曝露指標
        if valid_pm25_list:
            avg_exposure = float(np.mean(valid_pm25_list))
            max_exposure = float(np.max(valid_pm25_list))
        else:
            # 萬一該路線周邊完全沒有任何移動感測器覆蓋，則給予基準背景值(例如總平均)
            avg_exposure = float(df_sensors['pm25'].mean())
            max_exposure = float(df_sensors['pm25'].max())
            
        evaluated_results.append({
            "route_id": route.route_id,
            "average_exposure_pm25": round(avg_exposure, 2),
            "peak_exposure_pm25": round(max_exposure, 2),
            "analyzed_points_count": len(route.coordinates)
        })
        
    # 5. 根據平均曝露值由低到高排序 (越健康排在越前面)
    evaluated_results.sort(key=lambda x: x["average_exposure_pm25"])
    
    # 6. 動態注入決策標籤
    for i, res in enumerate(evaluated_results):
        res["is_ai_recommended"] = (i == 0) # 曝露值最低者獲選為 AI 最佳健康推薦路徑
        
    return {
        "status": "success",
        "results": evaluated_results
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main.py", host="0.0.0.0", port=8000, reload=True)
