// js/map.js
// ══════════════════════════════════════════════════════════════
// Leaflet 地圖引擎 — 僅在 map.html 載入
// getColor / getStatus 已移至 common.js
// ══════════════════════════════════════════════════════════════

let mapInitialized = false;
let bounds = [];
let latestLatLng = null;

// ── 地圖顯示模式：'raw'（原始）或 'aggregate'（聚合）────────────
let MAP_DISPLAY_MODE = localStorage.getItem('mapDisplayMode') ?? 'aggregate';

// ── 條件初始化：只在 #map 元素存在時啟動 Leaflet ────────────────
let map = null;
const mapEl = document.getElementById('map');
if (mapEl && typeof L !== 'undefined') {
  map = L.map('map').setView([25.094, 121.546], 13);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19, attribution: '© OpenStreetMap'
  }).addTo(map);

  // 建立自訂圖層面板供路線使用
  map.createPane('routePane');
  map.getPane('routePane').style.zIndex = 450;
}

const markersLayer = (map) ? L.layerGroup().addTo(map) : null;

// ─────────────────────────────────────────────────────────────────
// clearMapMarkers
// ─────────────────────────────────────────────────────────────────
function clearMapMarkers() {
  if (!markersLayer) return;
  markersLayer.clearLayers();
  bounds = [];
  latestLatLng = null;
}

// ─────────────────────────────────────────────────────────────────
// 原始模式：逐點繪製
// ─────────────────────────────────────────────────────────────────
function addMarkerToMap(point, isLatest = false) {
  if (!map || !markersLayer) return;
  const pm25Val = point.pm25;
  const hotspotsEl = document.getElementById('setting-hotspots');
  const showHotspotsOnly = hotspotsEl ? hotspotsEl.checked : false;

  if (isLatest) latestLatLng = [point.latitude, point.longitude];
  bounds.push([point.latitude, point.longitude]);
  if (showHotspotsOnly && pm25Val < 35.5) return;

  const marker = L.circleMarker([point.latitude, point.longitude], {
    radius: 8, color: '#ffffff', weight: 1,
    fillColor: getColor(pm25Val), fillOpacity: 0.85
  }).addTo(markersLayer);

  marker.bindPopup(
    `<strong>PM2.5：${pm25Val} µg/m³</strong><br>` +
    `狀態：${getStatus(pm25Val)}<br>` +
    `時間：${point.timestamp || '--'}`
  );
  bounds.push([point.latitude, point.longitude]);
}

// ─────────────────────────────────────────────────────────────────
// 空間聚合函式
// ─────────────────────────────────────────────────────────────────
function aggregatePoints(dataArray) {
  const grid = {};

  for (const d of dataArray) {
    const lat = parseFloat(d.latitude);
    const lon = parseFloat(d.longitude);
    const pm25 = parseFloat(d.pm25);
    if (!isFinite(lat) || !isFinite(lon) || !isFinite(pm25)) continue;

    const key = `${lat.toFixed(3)},${lon.toFixed(3)}`;
    if (!grid[key]) {
      grid[key] = {
        latSum: 0, lonSum: 0,
        pm25Sum: 0, pm25Max: -Infinity,
        count: 0, latestTime: ''
      };
    }
    const g = grid[key];
    g.latSum  += lat;
    g.lonSum  += lon;
    g.pm25Sum += pm25;
    g.count   += 1;
    if (pm25 > g.pm25Max) g.pm25Max = pm25;
    if (!g.latestTime || (d.timestamp || '') > g.latestTime) {
      g.latestTime = d.timestamp || '';
    }
  }

  return Object.values(grid).map(g => ({
    latitude:  g.latSum  / g.count,
    longitude: g.lonSum  / g.count,
    pm25Avg:   g.pm25Sum / g.count,
    pm25Max:   g.pm25Max,
    count:     g.count,
    latestTime: g.latestTime,
  }));
}

// ─────────────────────────────────────────────────────────────────
// 聚合模式：批次繪製
// ─────────────────────────────────────────────────────────────────
function addAggregatedMarkersToMap(dataArray) {
  if (!map || !markersLayer) return;
  const hotspotsEl = document.getElementById('setting-hotspots');
  const showHotspotsOnly = hotspotsEl ? hotspotsEl.checked : false;
  const aggregated = aggregatePoints(dataArray);

  if (aggregated.length > 0) {
    const newest = [...aggregated].sort((a, b) =>
      (b.latestTime ?? '').localeCompare(a.latestTime ?? '')
    )[0];
    latestLatLng = [newest.latitude, newest.longitude];
  }

  for (const pt of aggregated) {
    const avg = pt.pm25Avg;
    const max = pt.pm25Max;
    bounds.push([pt.latitude, pt.longitude]);
    if (showHotspotsOnly && avg < 35.5) continue;

    const radius = Math.min(8 + (pt.count - 1) * 0.5, 18);
    const hasPeakAlert = max >= 35.5 && max > avg * 1.2;

    const marker = L.circleMarker([pt.latitude, pt.longitude], {
      radius,
      color:       hasPeakAlert ? '#d93025' : '#ffffff',
      weight:      hasPeakAlert ? 2 : 1,
      fillColor:   getColor(avg),
      fillOpacity: 0.85,
    }).addTo(markersLayer);

    const peakLine = hasPeakAlert
      ? `<span style="color:#d93025;font-weight:bold;">⚠️ 峰值：${max.toFixed(1)} µg/m³</span><br>`
      : (max > avg
        ? `峰值：${max.toFixed(1)} µg/m³<br>`
        : '');

    marker.bindPopup(
      `<strong>平均 PM2.5：${avg.toFixed(1)} µg/m³</strong><br>` +
      `${peakLine}` +
      `狀態：${getStatus(avg)}<br>` +
      `樣本數：${pt.count} 筆觀測<br>` +
      `最近更新：${pt.latestTime || '--'}`
    );

    bounds.push([pt.latitude, pt.longitude]);
  }
}

// ─────────────────────────────────────────────────────────────────
// renderMapMarkers：統一入口
// ─────────────────────────────────────────────────────────────────
function renderMapMarkers(dataArray) {
  if (!map) return;
  clearMapMarkers();
  if (MAP_DISPLAY_MODE === 'aggregate') {
    addAggregatedMarkersToMap(dataArray);
  } else {
    const reversedData = [...dataArray].reverse();
    reversedData.forEach((point, idx) => addMarkerToMap(point, idx === 0));
  }
}

// ─────────────────────────────────────────────────────────────────
// 顯示模式切換
// ─────────────────────────────────────────────────────────────────
function setMapDisplayMode(mode) {
  MAP_DISPLAY_MODE = mode;
  localStorage.setItem('mapDisplayMode', mode);
  if (typeof globalData !== 'undefined' && globalData.length > 0) {
    const cutoff = typeof getMapCutoffTime === 'function' ? getMapCutoffTime() : 0;
    const windowData = globalData.filter(d => {
      const t = new Date(d.timestamp).getTime();
      return !isNaN(t) && t >= cutoff;
    });
    renderMapMarkers(windowData);
    refreshMapLayout();
  }
}

// ─────────────────────────────────────────────────────────────────
// refreshMapLayout
// ─────────────────────────────────────────────────────────────────
function refreshMapLayout() {
  if (!map) return;
  setTimeout(() => {
    map.invalidateSize();
    const autoTrackEl = document.getElementById('setting-autotrack');
    const autoTrack = autoTrackEl ? autoTrackEl.checked : false;
    if (autoTrack && latestLatLng) {
      map.flyTo(latestLatLng, 16, { animate: true, duration: 1.2 });
    } else if (bounds.length > 0) {
      if (!mapInitialized) {
        map.fitBounds(bounds, { padding: [40, 40], maxZoom: 15 });
        mapInitialized = true;
      } else {
        const currentZoom = map.getZoom();
        if (currentZoom < 11) {
          map.fitBounds(bounds, { padding: [40, 40], maxZoom: 15 });
        }
      }
    }
  }, 150);
}