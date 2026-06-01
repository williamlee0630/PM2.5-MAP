// js/map.js
let mapInitialized = false;
let bounds = [];
let latestLatLng = null;

// ── 地圖顯示模式：'raw'（原始）或 'aggregate'（聚合）────────────
// 預設聚合模式，可在設定頁切換
let MAP_DISPLAY_MODE = localStorage.getItem('mapDisplayMode') ?? 'aggregate';

const map = L.map('map').setView([25.094, 121.546], 13);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19, attribution: '© OpenStreetMap'
}).addTo(map);

const markersLayer = L.layerGroup().addTo(map);

// ─────────────────────────────────────────────────────────────────
// 顏色 / 狀態輔助函式
// ─────────────────────────────────────────────────────────────────
function getColor(pm25) {
  if (pm25 < 15.5)  return '#00e400';
  if (pm25 < 35.5)  return '#ffff00';
  if (pm25 < 54.5)  return '#ff7e00';
  if (pm25 < 150.5) return '#ff0000';
  if (pm25 < 250.5) return '#8f3f97';
  return '#7e0023';
}

function getStatus(pm25) {
  if (pm25 < 15.5)  return '良好';
  if (pm25 < 35.5)  return '普通';
  if (pm25 < 54.5)  return '對敏感族群不健康';
  if (pm25 < 150.5) return '對所有族群不健康';
  if (pm25 < 250.5) return '非常不健康';
  return '危害';
}

// ─────────────────────────────────────────────────────────────────
// clearMapMarkers
// ─────────────────────────────────────────────────────────────────
function clearMapMarkers() {
  markersLayer.clearLayers();
  bounds = [];
  latestLatLng = null;
}

// ─────────────────────────────────────────────────────────────────
// 原始模式：逐點繪製
// ─────────────────────────────────────────────────────────────────
function addMarkerToMap(point, isLatest = false) {
  const pm25Val = point.pm25;
  const showHotspotsOnly = document.getElementById('setting-hotspots').checked;

  if (isLatest) latestLatLng = [point.latitude, point.longitude];
  // ★ 無論是否過濾，都先加入 bounds（讓 fitBounds 能縮放到資料所在位置）
  bounds.push([point.latitude, point.longitude]);
  if (showHotspotsOnly && pm25Val < 35.5) return;   // 只過濾繪製，不過濾 bounds

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
// 以 0.001°（≈111m）格子將相近座標合併：
//   - 圓點大小依樣本數縮放（1筆=8px，每增1筆+0.5px，最大18px）
//   - 顏色依平均 PM2.5
//   - 若峰值比平均高 ≥20% 且 ≥35.5，在右上角加小紅點峰值標記
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
  const showHotspotsOnly = document.getElementById('setting-hotspots').checked;
  const aggregated = aggregatePoints(dataArray);

  // 用最新的一筆設定 latestLatLng（原始資料已在 app.js 處理）
  if (aggregated.length > 0) {
    const newest = [...aggregated].sort((a, b) =>
      (b.latestTime ?? '').localeCompare(a.latestTime ?? '')
    )[0];
    latestLatLng = [newest.latitude, newest.longitude];
  }

  for (const pt of aggregated) {
    const avg = pt.pm25Avg;
    const max = pt.pm25Max;
    // ★ 無論是否過濾，先加入 bounds
    bounds.push([pt.latitude, pt.longitude]);
    if (showHotspotsOnly && avg < 35.5) continue;   // 只過濾繪製，不過濾 bounds

    // 圓點半徑：基礎 8，每筆 +0.5，上限 18
    const radius = Math.min(8 + (pt.count - 1) * 0.5, 18);
    // 峰值警示：峰值超過均值 20% 且峰值本身 ≥ 35.5
    const hasPeakAlert = max >= 35.5 && max > avg * 1.2;

    const marker = L.circleMarker([pt.latitude, pt.longitude], {
      radius,
      color:       hasPeakAlert ? '#d93025' : '#ffffff',
      weight:      hasPeakAlert ? 2 : 1,
      fillColor:   getColor(avg),
      fillOpacity: 0.85,
    }).addTo(markersLayer);

    // Popup：同時顯示平均值與峰值
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
// renderMapMarkers：統一入口，依模式選擇原始或聚合
// app.js 的 rebuildTable 改呼叫此函式取代直接呼叫 addMarkerToMap
// ─────────────────────────────────────────────────────────────────
function renderMapMarkers(dataArray) {
  clearMapMarkers();
  if (MAP_DISPLAY_MODE === 'aggregate') {
    addAggregatedMarkersToMap(dataArray);
  } else {
    // 原始模式：逐點繪製
    const reversedData = [...dataArray].reverse();
    reversedData.forEach((point, idx) => addMarkerToMap(point, idx === 0));
  }
}

// ─────────────────────────────────────────────────────────────────
// 顯示模式切換（由 index.html 設定頁呼叫）
// ─────────────────────────────────────────────────────────────────
function setMapDisplayMode(mode) {
  MAP_DISPLAY_MODE = mode;
  localStorage.setItem('mapDisplayMode', mode);
  // 通知 app.js 重繪（app.js 中 window.globalData 是全域）
  if (typeof globalData !== 'undefined' && globalData.length > 0) {
    // 取得目前時間視窗內的資料重繪
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
// refreshMapLayout（不變）
// ─────────────────────────────────────────────────────────────────
function refreshMapLayout() {
  setTimeout(() => {
    map.invalidateSize();
    const autoTrack = document.getElementById('setting-autotrack').checked;
    if (autoTrack && latestLatLng) {
      map.flyTo(latestLatLng, 16, { animate: true, duration: 1.2 });
    } else if (bounds.length > 0) {
      if (!mapInitialized) {
        // 第一次載入：完整 fitBounds
        map.fitBounds(bounds, { padding: [40, 40], maxZoom: 15 });
        mapInitialized = true;
      } else {
        const currentZoom = map.getZoom();
        // 地圖目前太遠（縮放 < 11 表示看不清個別點），自動縮進去
        if (currentZoom < 11) {
          map.fitBounds(bounds, { padding: [40, 40], maxZoom: 15 });
        }
      }
    }
  }, 150);
}