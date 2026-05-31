// js/app.js
const sheetId = '1jcRopeeqnT786iB9m6Jd8oQH34S2AUE9Sp5-4eCgwYQ';
const csvUrl = `https://docs.google.com/spreadsheets/d/${sheetId}/export?format=csv`;

// 註冊 DataLabels 套件 (為了圓餅圖顯示百分比)
Chart.register(ChartDataLabels);

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
    const weatherRes = await fetch('https://api.open-meteo.com/v1/forecast?latitude=25.0330&longitude=121.5654&current=temperature_2m,wind_speed_10m,weather_code&timezone=Asia%2FTaipei');
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
  document.getElementById(`chart-panel-${tabId}`).classList.add('active');

}

// ─────────────────────────────────────────────────────────────
// [新增] 日期篩選下拉選單動態填充
// 從 globalData 提取所有不重複日期，依序加入 #chartDateFilter
// ─────────────────────────────────────────────────────────────
function updateDateFilter(data) {
  const dateSelect = document.getElementById('chartDateFilter');
  if (!dateSelect) return;

  // 提取所有不重複、格式正確的日期字串 (e.g. "2026-05-29")
  const uniqueDates = [...new Set(
    data
      .map(d => d.timestamp ? d.timestamp.trim().split(/\s+/)[0] : null)
      .filter(d => d && /^\d{4}-\d{2}-\d{2}$/.test(d))
  )].sort();

  // 記住目前選擇，重建後盡量還原
  const prevValue = dateSelect.value;

  dateSelect.innerHTML = '<option value="all">所有日期</option>';
  uniqueDates.forEach(date => {
    const opt = document.createElement('option');
    opt.value = date;
    // "2026-05-29" → "05/29 (2026)"
    const [year, month, day] = date.split('-');
    opt.textContent = `${month}/${day} (${year})`;
    dateSelect.appendChild(opt);
  });

  // 若先前的選擇仍在新資料中則保留，否則回到「所有日期」
  if ([...dateSelect.options].some(o => o.value === prevValue)) {
    dateSelect.value = prevValue;
  }
}

// ─────────────────────────────────────────────────────────────
// 時間序列標籤格式化
// 同一天只顯示時間；跨日第一筆加上 MM/DD 前綴
// ─────────────────────────────────────────────────────────────
// 每筆都顯示完整日期時間 MM/DD HH:MM:SS
function formatTrendLabel(timestamp) {
  if (!timestamp) return '';
  const parts = timestamp.trim().split(/\s+/);
  if (parts.length < 2) return parts[0];
  const dateShort = parts[0].replace(/^\d{4}-/, '').replace('-', '/');
  return `${dateShort} ${parts[1]}`;
}

// ══════════════════════════════════════════════════════════════
// 縣市反查工具（GeoJSON 點位→縣市名稱）
// 供：地區篩選下拉、數據後台縣市欄位、CSV 下載
// ══════════════════════════════════════════════════════════════
let countyGeoJSON   = null;   // 快取的台灣鄉鎮市區 GeoJSON
let _countyCache    = {};     // { "lat,lon": "區名" } 避免重複運算

const COUNTY_GEOJSON_URL =
  'https://cdn.jsdelivr.net/gh/g0v/twgeojson@master/json/twCounty2010.geo.json';

// ── 射線法：判斷點 [x,y] 是否在多邊形環內 ───────────────────────
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

// ── 找出座標 (lon, lat) 屬於哪個縣市 ────────────────────────────
function getCountyName(lon, lat, features) {
  const pt = [lon, lat];
  for (const f of features) {
    const geo = f.geometry;
    if (!geo) continue;
    const polys = geo.type === 'MultiPolygon' ? geo.coordinates : [geo.coordinates];
    for (const poly of polys) {
      if (_pointInRing(pt, poly[0])) {
        // twTown 用 TOWNNAME（如「士林區」），縣市層級用 COUNTYNAME
        return f.properties.TOWNNAME ?? f.properties.COUNTYNAME ?? f.properties.name ?? null;
      }
    }
  }
  return null;
}

// ── 懶載入台灣縣市 GeoJSON（僅首次呼叫時下載）─────────────────
async function loadCountyGeoJSONIfNeeded() {
  if (countyGeoJSON) return true;
  try {
    // twTown2010.geo.json：鄉鎮市區層級，可細分到「區」
    const res = await fetch(
      'https://cdn.jsdelivr.net/gh/g0v/twgeojson@master/json/twTown2010.geo.json'
    );
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    countyGeoJSON = await res.json();
    return true;
  } catch (e) {
    console.warn('縣市 GeoJSON 載入失敗（地區功能降級）:', e);
    return false;
  }
}

// ── 以快取+射線法查詢點位所屬縣市（精確到小數點後 4 位）──────────
function getPointCounty(lat, lon) {
  if (!countyGeoJSON) return '載入中';
  const key = `${parseFloat(lat).toFixed(4)},${parseFloat(lon).toFixed(4)}`;
  if (_countyCache[key] !== undefined) return _countyCache[key];
  const result = getCountyName(parseFloat(lon), parseFloat(lat), countyGeoJSON.features) ?? '其他';
  _countyCache[key] = result;
  return result;
}

// ── 非同步將縣市名稱寫入 globalData._county，並更新地區篩選選單 ──
async function enrichDataWithCounty() {
  const ok = await loadCountyGeoJSONIfNeeded();
  if (!ok) return;
  globalData.forEach(d => {
    if (!d._county) d._county = getPointCounty(d.latitude, d.longitude);
  });
  updateRegionFilter();
}

// ── 地區篩選下拉選單動態重建 ─────────────────────────────────────
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

// ── CSV 下載（含縣市欄位，BOM 確保 Excel 正確顯示繁體中文）────────
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
  if (globalData.length === 0) return;

  // 彙總各區平均 PM2.5
  const buckets = {};
  globalData.forEach(d => {
    const district = d._county;   // enrichDataWithCounty 存在 _county
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

  // 依平均 PM2.5 降序排列
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
        // 35.5 µg/m³ AQI 警戒線
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
          // 手動畫警戒線用 afterDraw plugin
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
  if (globalData.length === 0) return;

  // ── 1. PM2.5 濃度篩選 ──
  const filterElement = document.getElementById('pm25TrendFilter');
  const filterValue = filterElement ? filterElement.value : 'all';

  let trendData = globalData;
  if (filterValue === 'warning') {
    trendData = globalData.filter(d => d.pm25 >= 35.5);
  } else if (filterValue === 'danger') {
    trendData = globalData.filter(d => d.pm25 >= 54.5);
  }

  // ── 1b. 地區篩選 ──
  const regionEl = document.getElementById('chartRegionFilter');
  const regionValue = regionEl ? regionEl.value : 'all';
  if (regionValue !== 'all') {
    trendData = trendData.filter(d => d._county === regionValue);
  }

  // ── 2. 日期篩選 (新增) ──
  // 選定特定日期時，時段篩選（1h/6h/24h）自動停用，因為範圍已限縮為單日
  const dateFilterEl  = document.getElementById('chartDateFilter');
  const timeFilterEl  = document.getElementById('chartTimeRangeFilter');
  const dateFilterValue = dateFilterEl ? dateFilterEl.value : 'all';
  const timeFilterValue = timeFilterEl ? timeFilterEl.value : 'all';

  if (dateFilterValue !== 'all') {
    // 僅保留該日資料
    trendData = trendData.filter(d => {
      if (!d.timestamp) return false;
      return d.timestamp.trim().split(/\s+/)[0] === dateFilterValue;
    });
    // 停用時段篩選，避免誤導（選特定日期後「最近 1 小時」無意義）
    if (timeFilterEl) {
      timeFilterEl.disabled = true;
      timeFilterEl.title = '已選擇特定日期，時段篩選暫停';
    }
  } else {
    // 恢復時段篩選
    if (timeFilterEl) {
      timeFilterEl.disabled = false;
      timeFilterEl.title = '';
    }

    // ── 3. 時段篩選（僅在「所有日期」時生效）──
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

  // ── 4. 標籤格式化（含跨日標示）──
  const labels = trendData.map(d => formatTrendLabel(d.timestamp));
  const pm25Values = trendData.map(d => d.pm25);

  // ── 5. 圓餅圖統計（永遠基於 globalData，不受篩選影響）──
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
  if (!tableBody) return;

  // 重建前記住捲動位置，避免自動刷新時使用者被拉回頂部
  const scrollEl = document.getElementById('data-scroll-container');
  const savedTop = scrollEl ? scrollEl.scrollTop : 0;

  tableBody.innerHTML = '';
  const reversedData = [...globalData].reverse();
  reversedData.forEach(function(point, index) {
    addMarkerToMap(point, index === 0);
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

  // 還原捲動位置（requestAnimationFrame 確保 DOM 繪製完成後再還原）
  if (scrollEl && savedTop > 0) {
    requestAnimationFrame(() => { scrollEl.scrollTop = savedTop; });
  }
}

// === 抓取資料主函式 (PapaParse) ===
function fetchData(isAuto = false) {
  // ── 效能保護：路線計算進行中時，跳過自動更新 ──────────────────
  // isRoutingInProgress 由 index.html 的 callPythonRoutingAPI() 控制。
  // 路線計算需要 fetch + Python 運算，若同時跑 PapaParse + 重繪地圖
  // 會搶佔主執行緒與網路，導致計算顯著變慢。
  if (isAuto && typeof isRoutingInProgress !== 'undefined' && isRoutingInProgress) {
    return;
  }

  if (!isAuto) document.getElementById('data-status').innerText = '資料讀取中...';

  Papa.parse(csvUrl, {
    download: true,
    header: true,
    dynamicTyping: true,
    complete: function(results) {
      globalData = results.data.filter(row => row.latitude && row.longitude && row.pm25 !== undefined);
      
      document.getElementById('data-status').innerText = `共讀取 ${globalData.length} 筆資料 (最後更新: ${new Date().toLocaleTimeString()})`;

      // 重建日期選單
      updateDateFilter(globalData);

      const tableBody = document.getElementById('data-table-body');
      tableBody.innerHTML = '';

      clearMapMarkers();
      renderCharts();
      updateDynamicText(globalData);

      // 非同步富集縣市資料（GeoJSON 懶載入，不阻塞主流程）
      enrichDataWithCounty().then(() => {
        // GeoJSON 載入完成後重繪圖表（地區篩選選單已更新）
        renderCharts();
        // 重繪表格（縣市欄位現在有值了）
        rebuildTable();
        // 地區長條圖分頁（預設顯示）：縣市解析完成後一定渲染
        const _dPanel = document.getElementById('chart-panel-district');
        if (_dPanel && _dPanel.classList.contains('active')) renderDistrictBar();
      });

      // 先用現有資料渲染表格（縣市欄可能還是「載入中」）
      rebuildTable();

      if (document.getElementById('view-map').classList.contains('active')) {
        refreshMapLayout();
      }
    },
    error: function(err) {
      console.error(err);
      if (!isAuto) {
        document.getElementById('data-status').innerText = '讀取失敗，請確認 Google Sheets 權限';
        document.getElementById('data-status').style.color = 'red';
      }
    }
  });
}

function refreshData() { fetchData(false); }

document.addEventListener('DOMContentLoaded', () => {
  fetchData(false);
  setInterval(() => { fetchData(true); }, 10000);
});