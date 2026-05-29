// js/map.js
let mapInitialized = false; 
let bounds = []; 
let latestLatLng = null;

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
  latestLatLng = null;
  // ★ 修復點：已經移除了 mapInitialized = false; 
  // 這樣地圖就不會每 10 秒重置一次使用者的自訂視角了
}

function addMarkerToMap(point, isLatest = false) {
  const pm25Val = point.pm25;
  const showHotspotsOnly = document.getElementById('setting-hotspots').checked;

  if (isLatest) {
    latestLatLng = [point.latitude, point.longitude];
  }

  // 如果勾選僅顯示熱區，且數值小於 35.5 (良好或普通)，則不畫點
  if (showHotspotsOnly && pm25Val < 35.5) {
    return;
  }

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
}

function refreshMapLayout() {
  setTimeout(() => {
    map.invalidateSize(); 
    
    const autoTrack = document.getElementById('setting-autotrack').checked;
    
    if (autoTrack && latestLatLng) {
      // 若開啟自動追蹤，飛向最新點位
      map.flyTo(latestLatLng, 16, { animate: true, duration: 1.2 });
    } else if (!mapInitialized && bounds.length > 0) {
      // ★ 現在這個 fitBounds 只會在網頁「第一次」載入地圖時執行了
      map.fitBounds(bounds, { padding: [40, 40], maxZoom: 17 });
      mapInitialized = true; 
    }
    
  }, 100);
}