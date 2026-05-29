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
  // 移除所有 active 狀態
  document.querySelectorAll('.chart-tab').forEach(btn => btn.classList.remove('active'));
  document.querySelectorAll('.chart-panel').forEach(panel => panel.classList.remove('active'));
  
  // 啟動點擊的標籤與對應面板
  btnElement.classList.add('active');
  document.getElementById(`chart-panel-${tabId}`).classList.add('active');
}

// === 圖表渲染主函式 (含縮放與時段篩選) ===
function renderCharts() {
  if (globalData.length === 0) return;

  const filterElement = document.getElementById('pm25TrendFilter');
  const filterValue = filterElement ? filterElement.value : 'all';
  
  let trendData = globalData;
  if (filterValue === 'warning') {
    trendData = globalData.filter(d => d.pm25 >= 35.5);
  } else if (filterValue === 'danger') {
    trendData = globalData.filter(d => d.pm25 >= 54.5);
  }

  const timeFilterElement = document.getElementById('chartTimeRangeFilter');
  const timeFilterValue = timeFilterElement ? timeFilterElement.value : 'all';
  
  if (timeFilterValue !== 'all') {
    let pointsToKeep = trendData.length;
    if (timeFilterValue === '1h') pointsToKeep = 360; 
    else if (timeFilterValue === '6h') pointsToKeep = 2160; 
    else if (timeFilterValue === '24h') pointsToKeep = 8640; 
    
    if (trendData.length > pointsToKeep) {
      trendData = trendData.slice(trendData.length - pointsToKeep);
    }
  }

  const labels = trendData.map(d => d.timestamp ? d.timestamp.split(' ')[1] || d.timestamp : '');
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
            pan: {
              enabled: true,
              mode: 'x',
            },
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
          y: {
            suggestedMax: 60 
          }
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
              let dataArr = ctx.chart.data.datasets[0].data;
              dataArr.map(data => { sum += data; });
              if (value === 0) return null;
              let percentage = (value * 100 / sum).toFixed(1) + "%";
              return percentage;
            }
          }
        } 
      }
    });
  }
}

// === 抓取資料主函式 (PapaParse) ===
function fetchData(isAuto = false) {
  if (!isAuto) document.getElementById('data-status').innerText = '資料讀取中...';

  Papa.parse(csvUrl, {
    download: true,
    header: true,
    dynamicTyping: true,
    complete: function(results) {
      globalData = results.data.filter(row => row.latitude && row.longitude && row.pm25 !== undefined);
      
      document.getElementById('data-status').innerText = `共讀取 ${globalData.length} 筆資料 (最後更新: ${new Date().toLocaleTimeString()})`;

      const tableBody = document.getElementById('data-table-body');
      tableBody.innerHTML = '';
      
      clearMapMarkers();
      renderCharts();
      updateDynamicText(globalData);

      const reversedData = [...globalData].reverse();
      reversedData.forEach(function(point, index) {
        addMarkerToMap(point, index === 0);
        
        const pm25Val = point.pm25;
        const statusTxt = getStatus(pm25Val);
        const colorHex = getColor(pm25Val);

        const tr = document.createElement('tr');
        tr.innerHTML = `
          <td>${point.timestamp || '--'}</td>
          <td style="font-weight: bold; font-size: 16px;">${pm25Val}</td>
          <td><span class="status-badge" style="background-color: ${colorHex}; color: ${pm25Val > 54.5 ? '#fff' : '#000'}">${statusTxt}</span></td>
          <td>${point.latitude}</td>
          <td>${point.longitude}</td>
          <td>${point.satellites || 0}</td>
        `;
        tableBody.appendChild(tr);
      });
      
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
