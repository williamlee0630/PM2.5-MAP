// js/app.js
// ══════════════════════════════════════════════════════════════
// 資料抓取、圖表渲染、表格、county enrichment
// 多頁式架構：自動偵測當前頁面所需功能
// getColor / getStatus 已移至 common.js
// ══════════════════════════════════════════════════════════════

const sheetId = '1jcRopeeqnT786iB9m6Jd8oQH34S2AUE9Sp5-4eCgwYQ';
const csvUrl = `https://docs.google.com/spreadsheets/d/${sheetId}/export?format=csv`;

// ── 地圖顯示時間視窗 ─────────────────────────────────────────
let MAP_DISPLAY_HOURS = parseInt(localStorage.getItem('mapDisplayHours') ?? '168', 10);

function getMapCutoffTime() {
  return Date.now() - MAP_DISPLAY_HOURS * 60 * 60 * 1000;
}

// ── 地圖時間視窗設定套用（settings.html / map.html 使用）──────
function applyMapHoursSetting(hours) {
  MAP_DISPLAY_HOURS = parseInt(hours, 10);
  localStorage.setItem('mapDisplayHours', hours);
  refreshData();
}

// ── 條件性註冊 Chart.js DataLabels 套件 ──────────────────────
if (typeof Chart !== 'undefined' && typeof ChartDataLabels !== 'undefined') {
  Chart.register(ChartDataLabels);
}

let trendChartInstance = null;
let pieChartInstance = null;
let globalData = [];

// === 動態文字、AI 摘要與即時天氣運算邏輯 ===
async function updateDynamicText(data) {
  if (!data || data.length === 0) return;

  const latestData = data[data.length - 1];
  const latestTime = latestData.timestamp || new Date().toLocaleString('zh-TW');
  let sum = 0;
  let maxPm25 = -1;

  data.forEach(d => {
    if(d.pm25 !== undefined) {
      sum += d.pm25;
      if(d.pm25 > maxPm25) maxPm25 = d.pm25;
    }
  });
  const avgPm25 = (sum / data.length).toFixed(1);

  let insight = '';
  let aiSummary = '';
  if (maxPm25 >= 54.5) {
     insight = `⚠️ 警告：系統偵測到部分地區達 ${maxPm25} µg/m³ (不健康等級)，請敏感族群留意即時地圖！`;
     aiSummary = `根據「空污共犯」微型感測網最新數據（更新時間：${latestTime}）顯示，目前系統偵測到局部區域 PM2.5 濃度高達 <strong>${maxPm25} µg/m³ (對所有族群不健康)</strong>。強烈提醒機車族群與外出民眾可搭配本平台的「即時地圖」功能，避開潛在的空污熱區。`;
  } else if (maxPm25 >= 35.5) {
     insight = `⚡ 提醒：部分交通熱點 PM2.5 濃度升高，最高測得 ${maxPm25} µg/m³，建議行經周邊配戴口罩。`;
     aiSummary = `根據「空污共犯」微型感測網最新數據（更新時間：${latestTime}）顯示，大台北地區多數巷弄空氣品質尚可，但部分交通熱點偶有短暫的 PM2.5 濃度升高（最高測得 <strong>${maxPm25} µg/m³</strong>）。建議機車族群多加留意，並可利用地圖功能查詢最新狀態。`;
  } else {
     insight = `🌿 系統觀測中：多數觀測點維持在普通至良好等級 (最高僅 ${maxPm25} µg/m³)，空氣品質穩定。`;
     aiSummary = `根據「空污共犯」微型感測網最新數據（更新時間：${latestTime}）顯示，目前大台北地區各觀測點的 PM2.5 數值皆維持在普通至良好等級以內，平均濃度約為 <strong>${avgPm25} µg/m³</strong>，整體空氣品質十分穩定，適合戶外活動。`;
  }

  const marqueeEl = document.getElementById('dynamic-marquee');
  if (marqueeEl) marqueeEl.innerHTML = `🟢 系統狀態：感測網連線正常 ｜ 最新資料同步：${latestTime} ｜ AI 洞察：${insight}`;
  
  const summaryEl = document.getElementById('ai-summary-text');
  if (summaryEl) summaryEl.innerHTML = aiSummary;

  try {
    const weatherRes = await fetch('https://pm2-5-map.onrender.com/api/weather');
    const weatherData = await weatherRes.json();
    const current = weatherData.current;
    
    const temp = current.temperature_2m;
    const wind = current.wind_speed_10m;
    const weatherCode = current.weather_code;
    const updateTime = new Date().toLocaleString('zh-TW');

    let weatherDesc = '晴到多雲';
    if (weatherCode >= 51 && weatherCode <= 69) weatherDesc = '局部短暫雨';
    else if (weatherCode >= 71) weatherDesc = '大雨或雷雨';
    else if (weatherCode >= 41 && weatherCode <= 48) weatherDesc = '霧或霾';

    const timeEl = document.querySelector('.weather-suggestion-box .update-time');
    const descEl = document.getElementById('weather-realtime-desc');
    const adviceEl = document.getElementById('weather-realtime-advice');

    if (timeEl) timeEl.innerText = `即時資料連線時間：${updateTime}`;
    if (descEl) descEl.innerHTML = `今日台北地區即時天氣：<strong>${weatherDesc}</strong>，氣溫約 <strong>${temp}°C</strong>，風速 <strong>${wind} km/h</strong>。<br>根據「空污共犯」最新數據，目前大台北平均 PM2.5 濃度約為 <strong>${avgPm25} µg/m³</strong>。`;

    if (adviceEl) {
      if (avgPm25 > 35.4) {
        adviceEl.innerHTML = `<strong>⚠️ 防護建議：</strong> 目前空污濃度偏高，且氣溫為 ${temp}°C。強烈建議機車族與戶外活動者配戴口罩，並減少劇烈運動。`;
      } else if (weatherCode > 50) {
         adviceEl.innerHTML = `<strong>☔ 防護建議：</strong> 空氣品質尚可，但目前有降雨機率，外出請記得攜帶雨具，並留意天雨路滑。`;
      } else {
         adviceEl.innerHTML = `<strong>🌿 防護建議：</strong> 氣候舒適且空氣品質穩定，非常適合安排戶外活動、散步或短程城市探索！`;
      }
    }
  } catch (error) {
    console.error('天氣 API 讀取失敗', error);
  }
}

// === 圖表頁籤切換邏輯 ===
function switchChartTab(tabId, btnElement) {
  document.querySelectorAll('.chart-tab').forEach(btn => btn.classList.remove('active'));
  document.querySelectorAll('.chart-panel').forEach(panel => panel.classList.remove('active'));
  btnElement.classList.add('active');
  const panel = document.getElementById(`chart-panel-${tabId}`);
  if (panel) panel.classList.add('active');
}

// ─────────────────────────────────────────────────────────────
// 日期篩選下拉選單動態填充
// ─────────────────────────────────────────────────────────────
function updateDateFilter(data) {
  const dateSelect = document.getElementById('chartDateFilter');
  if (!dateSelect) return;

  const uniqueDates = [...new Set(
    data
      .map(d => d.timestamp ? d.timestamp.trim().split(/\s+/)[0] : null)
      .filter(d => d && /^\d{4}-\d{2}-\d{2}$/.test(d))
  )].sort();

  const prevValue = dateSelect.value;

  dateSelect.innerHTML = '<option value="all">所有日期</option>';
  uniqueDates.forEach(date => {
    const opt = document.createElement('option');
    opt.value = date;
    const [year, month, day] = date.split('-');
    opt.textContent = `${month}/${day} (${year})`;
    dateSelect.appendChild(opt);
  });

  if ([...dateSelect.options].some(o => o.value === prevValue)) {
    dateSelect.value = prevValue;
  }
}

// ─────────────────────────────────────────────────────────────
// 時間序列標籤格式化
// ─────────────────────────────────────────────────────────────
function formatTrendLabel(timestamp) {
  if (!timestamp) return '';
  const parts = timestamp.trim().split(/\s+/);
  if (parts.length < 2) return parts[0];
  const dateShort = parts[0].replace(/^\d{4}-/, '').replace('-', '/');
  return `${dateShort} ${parts[1]}`;
}

// ══════════════════════════════════════════════════════════════
// 縣市反查工具（使用自己 Repo 內的精確 GeoJSON）
// ══════════════════════════════════════════════════════════════
let countyGeoJSON = null;
let _countyCache  = {};

function _pointInRing([x, y], ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i], [xj, yj] = ring[j];
    if (((yi > y) !== (yj > y)) &&
        x < (xj - xi) * (y - yi) / (yj - yi) + xi) {
      inside = !inside;
    }
  }
  return inside;
}

function getCountyName(lon, lat, features) {
  const pt = [lon, lat];
  for (const f of features) {
    const geo = f.geometry;
    if (!geo) continue;
    const polys = geo.type === 'MultiPolygon' ? geo.coordinates : [geo.coordinates];
    for (const poly of polys) {
      if (_pointInRing(pt, poly[0])) {
        return f.properties.TOWNNAME ?? f.properties.COUNTYNAME ?? null;
      }
    }
  }
  return null;
}

async function loadCountyGeoJSONIfNeeded() {
  if (countyGeoJSON) return true;
  try {
    const res = await fetch('/data/twTown.geojson');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    countyGeoJSON = await res.json();
    console.log(`✅ twTown.geojson 載入成功（${countyGeoJSON.features.length} 個行政區）`);
    return true;
  } catch (e) {
    console.error('twTown.geojson 載入失敗:', e.message);
    return false;
  }
}

function getPointCounty(lat, lon) {
  if (!countyGeoJSON) return '載入中';
  const key = `${parseFloat(lat).toFixed(4)},${parseFloat(lon).toFixed(4)}`;
  if (_countyCache[key] !== undefined) return _countyCache[key];
  const result = getCountyName(parseFloat(lon), parseFloat(lat), countyGeoJSON.features) ?? '其他';
  _countyCache[key] = result;
  return result;
}

async function enrichDataWithCounty() {
  const ok = await loadCountyGeoJSONIfNeeded();
  if (!ok) return;

  globalData.forEach(d => {
    if (!d._county) {
      d._county = getPointCounty(d.latitude, d.longitude);
    }
  });

  updateRegionFilter();
}

function updateRegionFilter() {
  const sel = document.getElementById('chartRegionFilter');
  if (!sel) return;
  const prev = sel.value;
  const counties = [...new Set(globalData.map(d => d._county).filter(Boolean).filter(c => c !== '載入中'))].sort();
  sel.innerHTML = '<option value="all">全部地區</option>';
  counties.forEach(c => {
    const opt = document.createElement('option');
    opt.value = c; opt.textContent = c;
    sel.appendChild(opt);
  });
  if ([...sel.options].some(o => o.value === prev)) sel.value = prev;
}

// ── CSV 下載 ─────────────────────────────────────────────────
function downloadCSV() {
  const headers = ['時間戳記', 'PM2.5 (µg/m³)', '縣市地區', '狀態等級', '緯度', '經度', '衛星數'];
  const rows = [...globalData].reverse().map(d => [
    d.timestamp ?? '',
    d.pm25 ?? '',
    d._county ?? '未知',
    getStatus(d.pm25),
    d.latitude ?? '',
    d.longitude ?? '',
    d.satellites ?? ''
  ]);
  const csv = [headers, ...rows]
    .map(row => row.map(v => `"${String(v).replace(/"/g, '""')}"`).join(','))
    .join('\n');
  const blob = new Blob(['\uFEFF' + csv], { type: 'text/csv;charset=utf-8;' });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  a.href     = url;
  a.download = `空污共犯_PM25數據_${new Date().toLocaleDateString('zh-TW').replace(/\//g, '')}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// === 地區 PM2.5 比較長條圖 ===
let districtBarChart = null;

function renderDistrictBar() {
  if (typeof Chart === 'undefined' || globalData.length === 0) return;

  const buckets = {};
  globalData.forEach(d => {
    const district = d._county;
    if (!district || district === '載入中' || district === '其他') return;
    if (!buckets[district]) buckets[district] = { sum: 0, count: 0 };
    buckets[district].sum   += parseFloat(d.pm25) || 0;
    buckets[district].count += 1;
  });

  if (Object.keys(buckets).length === 0) {
    const ctx = document.getElementById('districtBarChart');
    if (ctx) {
      const p = ctx.parentElement.querySelector('.district-bar-notice');
      if (!p) {
        const notice = document.createElement('p');
        notice.className = 'district-bar-notice';
        notice.style.cssText = 'text-align:center;color:#aaa;padding:60px 0;font-size:14px;';
        notice.textContent = '⏳ 縣市資料解析中，請稍候…';
        ctx.parentElement.insertBefore(notice, ctx);
      }
    }
    return;
  }

  const sorted = Object.entries(buckets)
    .map(([name, v]) => ({ name, avg: +(v.sum / v.count).toFixed(1), count: v.count }))
    .sort((a, b) => b.avg - a.avg);

  const labels     = sorted.map(d => d.name);
  const values     = sorted.map(d => d.avg);
  const counts     = sorted.map(d => d.count);
  const bgColors   = values.map(v => {
    if (v >= 150.5) return 'rgba(143,63,151,0.8)';
    if (v >= 54.5)  return 'rgba(255,0,0,0.8)';
    if (v >= 35.5)  return 'rgba(255,126,0,0.8)';
    if (v >= 15.5)  return 'rgba(220,220,0,0.85)';
    return 'rgba(0,200,0,0.8)';
  });

  if (districtBarChart) districtBarChart.destroy();
  const ctx = document.getElementById('districtBarChart');
  if (!ctx) return;

  districtBarChart = new Chart(ctx.getContext('2d'), {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        label: '平均 PM2.5（µg/m³）',
        data: values,
        backgroundColor: bgColors,
        borderRadius: 6,
        borderSkipped: false,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        title: {
          display: true,
          text: '各行政區 PM2.5 平均濃度比較',
          font: { size: 16 }
        },
        datalabels: {
          anchor: 'end', align: 'end',
          color: '#333',
          font: { weight: 'bold', size: 11 },
          formatter: (v, ctx) => {
            const i = ctx.dataIndex;
            return `${v}\n(${counts[i]}筆)`;
          }
        },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const i = ctx.dataIndex;
              return [
                `平均 PM2.5：${values[i]} µg/m³`,
                `狀態：${getStatus(values[i])}`,
                `感測筆數：${counts[i]} 筆`
              ];
            }
          }
        },
        annotation: undefined,
      },
      scales: {
        x: {
          grid: { display: false },
          ticks: { font: { size: window.innerWidth <= 768 ? 10 : 13 } }
        },
        y: {
          suggestedMin: 0,
          suggestedMax: Math.max(...values, 60) * 1.15,
          ticks: { callback: v => `${v}` },
        }
      }
    },
    plugins: [{
      id: 'aqiLine',
      afterDraw(chart) {
        const { ctx, scales: { y } } = chart;
        const yPx355 = y.getPixelForValue(35.5);
        ctx.save();
        ctx.setLineDash([6, 4]);
        ctx.strokeStyle = '#ff7e00';
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.moveTo(chart.chartArea.left, yPx355);
        ctx.lineTo(chart.chartArea.right, yPx355);
        ctx.stroke();
        ctx.fillStyle = '#ff7e00';
        ctx.font = 'bold 11px "Microsoft JhengHei",sans-serif';
        ctx.fillText('敏感族群警戒 35.5', chart.chartArea.right - 145, yPx355 - 5);
        ctx.restore();
      }
    }]
  });
}

// === 圖表渲染主函式 ===
function renderCharts() {
  if (typeof Chart === 'undefined' || globalData.length === 0) return;

  const filterElement = document.getElementById('pm25TrendFilter');
  const filterValue = filterElement ? filterElement.value : 'all';

  let trendData = globalData;
  if (filterValue === 'warning') {
    trendData = globalData.filter(d => d.pm25 >= 35.5);
  } else if (filterValue === 'danger') {
    trendData = globalData.filter(d => d.pm25 >= 54.5);
  }

  const regionEl = document.getElementById('chartRegionFilter');
  const regionValue = regionEl ? regionEl.value : 'all';
  if (regionValue !== 'all') {
    trendData = trendData.filter(d => d._county === regionValue);
  }

  const dateFilterEl  = document.getElementById('chartDateFilter');
  const timeFilterEl  = document.getElementById('chartTimeRangeFilter');
  const dateFilterValue = dateFilterEl ? dateFilterEl.value : 'all';
  const timeFilterValue = timeFilterEl ? timeFilterEl.value : 'all';

  if (dateFilterValue !== 'all') {
    trendData = trendData.filter(d => {
      if (!d.timestamp) return false;
      return d.timestamp.trim().split(/\s+/)[0] === dateFilterValue;
    });
    if (timeFilterEl) {
      timeFilterEl.disabled = true;
      timeFilterEl.title = '已選擇特定日期，時段篩選暫停';
    }
  } else {
    if (timeFilterEl) {
      timeFilterEl.disabled = false;
      timeFilterEl.title = '';
    }

    if (timeFilterValue !== 'all') {
      let pointsToKeep = trendData.length;
      if (timeFilterValue === '1h')  pointsToKeep = 360;
      else if (timeFilterValue === '6h')  pointsToKeep = 2160;
      else if (timeFilterValue === '24h') pointsToKeep = 8640;

      if (trendData.length > pointsToKeep) {
        trendData = trendData.slice(trendData.length - pointsToKeep);
      }
    }
  }

  const labels = trendData.map(d => formatTrendLabel(d.timestamp));
  const pm25Values = trendData.map(d => d.pm25);

  const statusCounts = { '良好': 0, '普通': 0, '對敏感族群不健康': 0, '對所有族群不健康': 0, '非常不健康': 0, '危害': 0 };
  const statusColors = ['#00e400', '#ffff00', '#ff7e00', '#ff0000', '#8f3f97', '#7e0023'];
  globalData.forEach(d => {
    const status = getStatus(d.pm25);
    if (statusCounts[status] !== undefined) statusCounts[status]++;
  });

  if (trendChartInstance) trendChartInstance.destroy();
  if (pieChartInstance) pieChartInstance.destroy();

  const isMobile = window.innerWidth <= 768;

  const ctxTrend = document.getElementById('pm25TrendChart');
  if (ctxTrend) {
    trendChartInstance = new Chart(ctxTrend.getContext('2d'), {
      type: 'line',
      data: {
        labels: labels,
        datasets: [{ 
          label: 'PM2.5 濃度 (µg/m³)', 
          data: pm25Values, 
          borderColor: '#3498db', 
          backgroundColor: 'rgba(52, 152, 219, 0.2)', 
          borderWidth: isMobile ? 1.5 : 2, 
          pointRadius: isMobile ? 2 : 4,   
          tension: 0.3, 
          fill: true 
        }]
      },
      options: { 
        responsive: true, 
        maintainAspectRatio: false, 
        plugins: { 
          title: { display: true, text: 'PM2.5 濃度時間趨勢 (可使用滾輪縮放與滑鼠拖曳平移)', font: { size: 16 } },
          datalabels: { display: false },
          zoom: {
            pan: { enabled: true, mode: 'x' },
            zoom: {
              wheel: { enabled: true },
              pinch: { enabled: true },
              mode: 'x',
            }
          }
        },
        scales: {
          x: {
            ticks: {
              maxTicksLimit: isMobile ? 6 : 15,
              maxRotation: 45, 
              minRotation: 0
            }
          },
          y: { suggestedMax: 60 }
        }
      }
    });
  }

  const ctxPie = document.getElementById('statusPieChart');
  if (ctxPie) {
    pieChartInstance = new Chart(ctxPie.getContext('2d'), {
      type: 'doughnut',
      data: {
        labels: Object.keys(statusCounts),
        datasets: [{ data: Object.values(statusCounts), backgroundColor: statusColors, borderWidth: 1 }]
      },
      options: { 
        responsive: true, 
        maintainAspectRatio: false, 
        plugins: { 
          title: { display: true, text: '總體空氣品質狀態分佈', font: { size: 16 } }, 
          legend: { position: isMobile ? 'bottom' : 'right' },
          datalabels: {
            color: '#ffffff',
            font: { weight: 'bold', size: 14 },
            textStrokeColor: 'rgba(0,0,0,0.5)',
            textStrokeWidth: 3,
            formatter: (value, ctx) => {
              let sum = 0;
              ctx.chart.data.datasets[0].data.forEach(v => { sum += v; });
              if (value === 0) return null;
              return (value * 100 / sum).toFixed(1) + '%';
            }
          }
        } 
      }
    });
  }
}

// === 重繪數據後台表格（含縣市欄位）===
function rebuildTable() {
  const tableBody = document.getElementById('data-table-body');
  
  // ── 地圖繪製（僅在 map 存在時執行）──
  if (typeof renderMapMarkers === 'function') {
    const cutoff     = getMapCutoffTime();
    const windowData = globalData.filter(d => {
      const t = new Date(d.timestamp).getTime();
      return !isNaN(t) && t >= cutoff;
    });
    renderMapMarkers(windowData);

    const mapView = document.getElementById('view-map');
    if (mapView && mapView.classList.contains('active')) {
      if (typeof refreshMapLayout === 'function') refreshMapLayout();
    }
  }

  // ── 表格繪製（僅在 data-table-body 存在時執行）──
  if (!tableBody) return;

  const scrollEl = document.getElementById('data-scroll-container');
  const savedTop = scrollEl ? scrollEl.scrollTop : 0;

  tableBody.innerHTML = '';
  const reversedData = [...globalData].reverse();

  reversedData.forEach(function(point, index) {
    const pm25Val  = point.pm25;
    const statusTxt = getStatus(pm25Val);
    const colorHex  = getColor(pm25Val);
    const countyTxt = point._county || '<span style="color:#aaa;font-size:12px;">解析中</span>';
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${point.timestamp || '--'}</td>
      <td style="font-weight:bold;font-size:16px;">${pm25Val}</td>
      <td>${countyTxt}</td>
      <td><span class="status-badge" style="background-color:${colorHex};color:${pm25Val > 54.5 ? '#fff' : '#000'}">${statusTxt}</span></td>
      <td>${point.latitude}</td>
      <td>${point.longitude}</td>
      <td>${point.satellites || 0}</td>
    `;
    tableBody.appendChild(tr);
  });

  if (scrollEl && savedTop > 0) {
    requestAnimationFrame(() => { scrollEl.scrollTop = savedTop; });
  }
}

// === 抓取資料主函式 (PapaParse) ===
function fetchData(isAuto = false) {
  if (isAuto && typeof isRoutingInProgress !== 'undefined' && isRoutingInProgress) {
    return;
  }

  const statusEl = document.getElementById('data-status');
  if (!isAuto && statusEl) statusEl.innerText = '資料讀取中...';

  Papa.parse(csvUrl, {
    download: true,
    header: true,
    dynamicTyping: true,
    complete: function(results) {
      globalData = results.data.filter(row => row.latitude && row.longitude && row.pm25 !== undefined);
      
      if (statusEl) statusEl.innerText = `共讀取 ${globalData.length} 筆資料 (最後更新: ${new Date().toLocaleTimeString()})`;

      updateDateFilter(globalData);

      const tableBody = document.getElementById('data-table-body');
      if (tableBody) tableBody.innerHTML = '';

      if (typeof clearMapMarkers === 'function') clearMapMarkers();
      renderCharts();
      updateDynamicText(globalData);

      enrichDataWithCounty().then(() => {
        renderCharts();
        rebuildTable();
        const _dPanel = document.getElementById('chart-panel-district');
        if (_dPanel && _dPanel.classList.contains('active')) renderDistrictBar();
      });

      rebuildTable();

      const mapView = document.getElementById('view-map');
      if (mapView && mapView.classList.contains('active')) {
        if (typeof refreshMapLayout === 'function') refreshMapLayout();
      }
    },
    error: function(err) {
      console.error(err);
      if (!isAuto && statusEl) {
        statusEl.innerText = '讀取失敗，請確認 Google Sheets 權限';
        statusEl.style.color = 'red';
      }
    }
  });
}

function refreshData() { fetchData(false); }

document.addEventListener('DOMContentLoaded', () => {
  fetchData(false);
  setInterval(() => { fetchData(true); }, 10000);

  // 還原地圖設定（如果設定 DOM 元素存在）
  const savedHours = localStorage.getItem('mapDisplayHours');
  if (savedHours) {
    MAP_DISPLAY_HOURS = parseInt(savedHours, 10);
    const sel = document.getElementById('setting-map-hours');
    if (sel) sel.value = savedHours;
  }
  const savedMode = localStorage.getItem('mapDisplayMode');
  if (savedMode) {
    const modeSel = document.getElementById('setting-map-mode');
    if (modeSel) modeSel.value = savedMode;
  }
});