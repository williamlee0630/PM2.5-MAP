// js/routing-engine.js
// ══════════════════════════════════════════════════════════════
// 健康防護導航引擎 — routing.html 與 map.html 共用
// 從 index.html 行內 JS 抽出
// ══════════════════════════════════════════════════════════════

// ── 全域狀態 ─────────────────────────────────────────────────
let routePolylines = {};
let pendingRouteCoords = {};
let selectedRouteId = null;
let isRoutingInProgress = false;

// ── 路線選取函式（切換實線 / 虛線）──────────────────────────
function selectRoute(routeId) {
  selectedRouteId = routeId;

  Object.entries(routePolylines).forEach(([id, poly]) => {
    const isSelected = (id === routeId);
    const isRec = poly._pmIsRecommended;
    if (isSelected) {
      poly.setStyle({
        color: isRec ? '#0f9d58' : '#1a73e8',
        weight: 7,
        opacity: 0.95,
        dashArray: null,
      });
      poly.bringToFront();
    } else {
      poly.setStyle({ opacity: 0, fillOpacity: 0 });
    }
  });

  document.querySelectorAll('.route-result-card').forEach(card => {
    card.classList.toggle('selected', card.dataset.routeId === routeId);
  });

  const poly = routePolylines[routeId];
  if (poly && typeof map !== 'undefined' && map) {
    map.fitBounds(poly.getBounds(), { padding: [50, 50] });
  }
}

// ★ 地址轉經緯度
async function geocodeAddress(address) {
  const url = `https://pm2-5-map.onrender.com/api/geocode?q=${encodeURIComponent(address)}`;
  const res = await fetch(url);
  if (!res.ok) {
    const errData = await res.json();
    throw new Error(errData.detail || `找不到地址：${address}`);
  }
  return await res.json();
}

// OSRM 呼叫
async function getOSRMRoute(startCoord, endCoord, mode, waypoint = null) {
  let coordStr;
  if (waypoint) {
    coordStr = `${startCoord.lon},${startCoord.lat};${waypoint.lon},${waypoint.lat};${endCoord.lon},${endCoord.lat}`;
  } else {
    coordStr = `${startCoord.lon},${startCoord.lat};${endCoord.lon},${endCoord.lat}`;
  }
  const alt = waypoint ? 'false' : 'true';
  const url = `https://router.project-osrm.org/route/v1/${mode}/${coordStr}?geometries=geojson&alternatives=${alt}&overview=full`;
  const res = await fetch(url);
  const data = await res.json();
  if (data.code !== 'Ok' || !data.routes?.length) return [];
  return data.routes;
}

// ★ 主邏輯：前端協調 OSRM + 後端感測評分
async function callPythonRoutingAPI() {
  const btn = document.getElementById('btn-calc-route');
  const resultsContainer = document.getElementById('route-results-container');
  const wrapper = document.getElementById('route-cards-wrapper');

  const startAddress = document.getElementById('route-start').value.trim();
  const endAddress = document.getElementById('route-end').value.trim();
  const routeMode = document.getElementById('route-mode').value;

  if (!startAddress || !endAddress) { alert("請輸入完整的起點與目的地！"); return; }

  btn.disabled = true;
  btn.innerHTML = `<span class="material-symbols-outlined" style="animation:spin 1s linear infinite;">sync</span> 地理編碼與空間運算中...`;
  resultsContainer.classList.remove('active');
  isRoutingInProgress = true;

  // 清除舊路線（如果在 map.html 有 map 的話）
  Object.values(routePolylines).forEach(p => { if (typeof map !== 'undefined' && map) map.removeLayer(p); });
  routePolylines = {}; pendingRouteCoords = {}; selectedRouteId = null; wrapper.innerHTML = '';

  const modeIcon = routeMode === 'driving' ? '🚗' : (routeMode === 'cycling' ? '🚲' : '🚶');
  const strategyLabel = {
    'route_0': `${modeIcon} 最快路線`,
    'route_1': `${modeIcon} OSRM 替代路線`,
    'detour_A': `${modeIcon} 低污染繞路 A`,
    'detour_B': `${modeIcon} 低污染繞路 B`,
  };

  try {
    // ① 地理編碼
    const startCoord = await geocodeAddress(startAddress);
    const endCoord = await geocodeAddress(endAddress);

    // ② OSRM 基礎路線
    btn.innerHTML = `<span class="material-symbols-outlined" style="animation:spin 1s linear infinite;">sync</span> 取得基礎路線中...`;
    const baseRoutes = await getOSRMRoute(startCoord, endCoord, routeMode);
    if (!baseRoutes.length) throw new Error("OSRM 無法規劃此路段，請嘗試其他起終點");

    const toCoordObjs = coords => coords.map(([lat, lon]) => ({ lat, lon }));
    const toLLPairs = coords => coords.map(c => [c[1], c[0]]);

    const allRoutes = [];
    baseRoutes.slice(0, 2).forEach((r, i) => {
      allRoutes.push({ route_id: `route_${i}`, latLonPairs: toLLPairs(r.geometry.coordinates) });
    });

    // ③ 送主路線給後端偵測熱區
    btn.innerHTML = `<span class="material-symbols-outlined" style="animation:spin 1s linear infinite;">sync</span> 偵測空污熱區中...`;
    const mainCoords = toCoordObjs(allRoutes[0].latLonPairs);
    const hotspotRes = await fetch('https://pm2-5-map.onrender.com/api/detect-hotspots', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode: routeMode, coordinates: mainCoords })
    });
    const hotspotData = hotspotRes.ok ? await hotspotRes.json() : { waypoints: [] };
    const waypoints = hotspotData.waypoints ?? [];

    // ④ 用 waypoint 呼叫 OSRM 取得繞路路線
    if (waypoints.length) {
      btn.innerHTML = `<span class="material-symbols-outlined" style="animation:spin 1s linear infinite;">sync</span> 生成低污染繞路中...`;
      const detourPromises = waypoints.map(wp =>
        getOSRMRoute(startCoord, endCoord, routeMode, wp)
      );
      const detourResults = await Promise.allSettled(detourPromises);

      const directDistM = allRoutes[0]?.distM ?? allRoutes[0]?.latLonPairs?.length ?? 0;

      detourResults.forEach((result, idx) => {
        if (result.status !== 'fulfilled' || !result.value?.length) return;
        const wp = waypoints[idx];
        const rid = wp.label === 'detour_clean_A' ? 'detour_A' : 'detour_B';
        const distM = result.value[0].distance ?? 0;

        if (directDistM > 0 && distM > directDistM * 2.0) {
          console.log(`捨棄過長繞路：${(distM / 1000).toFixed(1)}km vs 直行 ${(directDistM / 1000).toFixed(1)}km`);
          return;
        }

        const alreadyHas = allRoutes.some(r => Math.abs((r.distM ?? 0) - distM) < 100);
        if (!alreadyHas) {
          allRoutes.push({
            route_id: rid,
            distM,
            latLonPairs: toLLPairs(result.value[0].geometry.coordinates)
          });
        }
      });
    }

    // ⑤ 送所有路線給後端統一評分
    btn.innerHTML = `<span class="material-symbols-outlined" style="animation:spin 1s linear infinite;">sync</span> 空污評分中...`;
    const scorePayload = {
      mode: routeMode,
      routes: allRoutes.map(r => ({
        route_id: r.route_id,
        coordinates: toCoordObjs(r.latLonPairs)
      }))
    };
    const scoreRes = await fetch('https://pm2-5-map.onrender.com/api/score-routes', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(scorePayload)
    });
    if (!scoreRes.ok) {
      const e = await scoreRes.json().catch(() => ({}));
      throw new Error(e.detail || "評分伺服器無回應");
    }
    const pyData = await scoreRes.json();
    const sortedResults = [...pyData.results].sort((a, b) =>
      a.is_ai_recommended === b.is_ai_recommended ? 0 : (a.is_ai_recommended ? -1 : 1)
    );

    // 保存座標對照
    allRoutes.forEach(r => { pendingRouteCoords[r.route_id] = r.latLonPairs; });

    // ── 存入 sessionStorage 供 map.html 使用 ──
    sessionStorage.setItem('routeResults', JSON.stringify(sortedResults));
    sessionStorage.setItem('routeCoords', JSON.stringify(pendingRouteCoords));
    sessionStorage.setItem('routeStrategyLabel', JSON.stringify(strategyLabel));

    // ── 單一路線提示
    if (sortedResults.length === 1) {
      const n = document.createElement('p');
      n.style.cssText = 'color:#5f6368;font-size:14px;margin-bottom:12px;';
      n.textContent = '⚠️ 此路段僅找到一條可用路線，無法進行替代比較';
      wrapper.appendChild(n);
    }

    // ── 口罩提醒
    const recRoute = sortedResults.find(r => r.is_ai_recommended);
    if (recRoute) {
      const peak = parseFloat(recRoute.peak_exposure_pm25);
      if (peak >= 54.5) {
        wrapper.insertAdjacentHTML('beforeend', `<div class="health-alert-mask"><span class="mask-icon">😷</span><div>
          <strong>⚠️ 強烈建議所有人佩戴口罩或 N95</strong>
          <p>推薦路線峰值 PM2.5 達 <strong>${escapeHtml(recRoute.peak_exposure_pm25)} µg/m³（對所有族群不健康）</strong>，請務必佩戴防護口罩。</p>
        </div></div>`);
      } else if (peak >= 35.5) {
        wrapper.insertAdjacentHTML('beforeend', `<div class="health-alert-mask"><span class="mask-icon">😷</span><div>
          <strong>建議敏感族群佩戴口罩</strong>
          <p>推薦路線峰值 PM2.5 達 <strong>${escapeHtml(recRoute.peak_exposure_pm25)} µg/m³（對敏感族群不健康）</strong>，氣喘患者及幼童請佩戴口罩。</p>
        </div></div>`);
      }
    }

    // ── 渲染卡片
    sortedResults.forEach(routeResult => {
      const isRec = routeResult.is_ai_recommended;
      const coverage = routeResult.data_coverage ?? 'unknown';
      const label = strategyLabel[routeResult.route_id] ?? `${modeIcon} 路線 (${routeResult.distance_km} km)`;
      const safeId = escapeHtml(`${label} (${routeResult.distance_km} km)`);
      const safeAvg = escapeHtml(routeResult.average_exposure_pm25);
      const safePeak = escapeHtml(routeResult.peak_exposure_pm25);
      const safeCount = escapeHtml(routeResult.analyzed_points_count);
      const safeTime = escapeHtml(routeResult.travel_time_minutes);
      const safeEI = escapeHtml(Math.round(routeResult.exposure_index));

      const coverageHtml = coverage === 'none'
        ? `<span class="coverage-warning">⚠️ 此路線無感測器覆蓋，數值為全區估算值</span>`
        : coverage === 'sparse' ? `<span class="coverage-warning">⚠️ 感測器覆蓋稀疏，數值僅供參考</span>` : '';

      const card = document.createElement('div');
      card.className = `route-result-card${isRec ? ' recommended' : ''}`;
      card.dataset.routeId = routeResult.route_id;
      // ★ 多頁式：點擊卡片跳到 map.html
      card.addEventListener('click', () => {
        sessionStorage.setItem('selectedRouteId', routeResult.route_id);
        window.location.href = 'map.html';
      });
      card.innerHTML = `
        <div class="route-result-info">
          <h4>${isRec ? '<span class="material-symbols-outlined" style="color:#0f9d58;">verified</span>' : ''} ${safeId}</h4>
          <p>行駛時間：${safeTime} 分 ｜ 比對 ${safeCount} 個感測點 ｜ 峰值：${safePeak} µg/m³</p>
          <p style="font-size:12px;color:#888;margin-top:4px;">暴露指數：${safeEI} µg/m³·s（PM2.5 × 行駛時間）</p>
          ${coverageHtml}
        </div>
        <div style="text-align:right;">
          <span style="font-size:13px;color:#5f6368;display:block;">預計平均曝露值</span>
          <span class="route-pm-score ${isRec ? 'healthy' : ''}">${safeAvg} <small style="font-size:14px;color:#5f6368;font-weight:normal;">µg/m³</small></span>
        </div>`;
      wrapper.appendChild(card);
    });

    resultsContainer.classList.add('active');
    alert("運算完成！請點擊路線卡片查看地圖上的路線標示。");

  } catch (err) {
    alert("發生錯誤：" + err.message);
    console.error(err);
  } finally {
    btn.disabled = false;
    btn.innerHTML = `<span class="material-symbols-outlined">route</span> 啟動演算法計算健康路線`;
    isRoutingInProgress = false;
  }
}

// ── map.html 載入時：從 sessionStorage 恢復路線 ──────────────
function restoreRoutesFromSession() {
  if (typeof map === 'undefined' || !map || typeof L === 'undefined') return;

  const storedResults = sessionStorage.getItem('routeResults');
  const storedCoords = sessionStorage.getItem('routeCoords');
  const storedLabels = sessionStorage.getItem('routeStrategyLabel');
  const selectedId = sessionStorage.getItem('selectedRouteId');

  if (!storedResults || !storedCoords) return;

  try {
    const results = JSON.parse(storedResults);
    const coords = JSON.parse(storedCoords);
    const labels = storedLabels ? JSON.parse(storedLabels) : {};

    // 繪製所有路線 polyline
    results.forEach(routeResult => {
      const routeCoords = routeResult.coordinates ?? coords[routeResult.route_id];
      if (!routeCoords) return;

      const isRec = routeResult.is_ai_recommended;
      const poly = L.polyline(routeCoords, {
        color: isRec ? '#0f9d58' : '#1a73e8',
        weight: 7,
        opacity: 0,
        dashArray: null,
        pane: 'routePane',
      }).addTo(map);
      poly._pmIsRecommended = isRec;
      routePolylines[routeResult.route_id] = poly;

      const safeAvg = escapeHtml(routeResult.average_exposure_pm25);
      const safeTime = escapeHtml(routeResult.travel_time_minutes);
      if (isRec) {
        poly.bindPopup(`<b>推薦健康路線</b><br>平均 PM2.5: ${safeAvg} µg/m³<br>行駛時間: ${safeTime} 分`);
      }
    });

    // 選取指定路線
    const targetId = selectedId || results.find(r => r.is_ai_recommended)?.route_id || results[0]?.route_id;
    if (targetId) selectRoute(targetId);

    // 清除 sessionStorage（避免重複載入）
    sessionStorage.removeItem('selectedRouteId');

  } catch (e) {
    console.error('路線資料恢復失敗:', e);
  }
}

// 當 map.html 的 DOM 載入完成時自動恢復路線
document.addEventListener('DOMContentLoaded', () => {
  // 延遲執行，確保 map.js 的地圖已初始化
  setTimeout(() => {
    if (typeof map !== 'undefined' && map) {
      restoreRoutesFromSession();
    }
  }, 500);
});
