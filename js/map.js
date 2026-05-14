// js/map.js
let mapInitialized = false; 
let bounds = []; 
let latestLatLng = null; // ★ 新增：用來紀錄最新一筆資料的座標

const map = L.map('map').setView([25.094, 121.546], 13);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19, attribution: '© OpenStreetMap'
}).addTo(map);

const markersLayer = L.layerGroup().addTo(map);

function getColor(pm25) {
  if (pm25 < 15.5) return '#00e400';
  if (pm25 < 35.5) return '#ffff00';
  if (pm25 < 54.5) return '#ff7e00';
  if (pm25 < 150.5) return '#ff0000';
  if (pm25 < 250.5) return '#8f3f97';
  return '#7e0023';
}

function getStatus(pm25) {
  if (pm25 < 15.5) return '良好';
  if (pm25 < 35.5) return '普通';
  if (pm25 < 54.5) return '對敏感族群不健康';
  if (pm25 < 150.5) return '對所有族群不健康';
  if (pm25 < 250.5) return '非常不健康';
  return '危害';
}

function clearMapMarkers() {
  markersLayer.clearLayers();
  bounds = [];
  latestLatLng = null; // ★ 清空時也一併忘記舊的最新座標
  mapInitialized = false;
}

// ★ 修改：加入 isLatest 參數，用來判斷這是不是最新資料
function addMarkerToMap(point, isLatest = false) {
  const pm25Val = point.pm25;
  const statusTxt = getStatus(pm25Val);
  const colorHex = getColor(pm25Val);

  const marker = L.circleMarker([point.latitude, point.longitude], {
    radius: 8, color: '#ffffff', weight: 1, fillColor: colorHex, fillOpacity: 0.85
  }).addTo(markersLayer);

  marker.bindPopup(
    `<strong>PM2.5：${pm25Val} µg/m³</strong><br>` +
    `狀態：${statusTxt}<br>時間：${point.timestamp || '--'}`
  );
  
  bounds.push([point.latitude, point.longitude]);

  // ★ 如果這是最新的一筆，就把它的經緯度存起來
  if (isLatest) {
    latestLatLng = [point.latitude, point.longitude];
  }
}

function refreshMapLayout() {
  setTimeout(() => {
    map.invalidateSize(); 
    
    // ★ 修改：優先檢查有沒有最新座標，有的話就「飛」過去
    if (latestLatLng) {
      // flyTo 會有平滑的移動動畫。16 是縮放級別 (越大越近)
      map.flyTo(latestLatLng, 16, { animate: true, duration: 1.2 });
    } else if (!mapInitialized && bounds.length > 0) {
      // 備用：如果沒有最新座標，就顯示全部點位
      map.fitBounds(bounds, { padding: [40, 40], maxZoom: 17 });
    }
    
    mapInitialized = true;
  }, 100);
}