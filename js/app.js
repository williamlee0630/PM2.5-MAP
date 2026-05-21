// js/app.js
const sheetId = '1jcRopeeqnT786iB9m6Jd8oQH34S2AUE9Sp5-4eCgwYQ';
const csvUrl = `https://docs.google.com/spreadsheets/d/${sheetId}/export?format=csv`;

// 註冊 DataLabels 套件 (為了圓餅圖顯示百分比)
Chart.register(ChartDataLabels);

let trendChartInstance = null;
let pieChartInstance = null;
let globalData = [];

// === 新增：動態文字與 AI 摘要運算邏輯 ===
function updateDynamicText(data) {
  if (!data || data.length === 0) return;

  // 取得最新一筆資料的時間
  const latestData = data[data.length - 1];
  const latestTime = latestData.timestamp || new Date().toLocaleString('zh-TW');

  // 計算平均與最大 PM2.5 數值
  let sum = 0;
  let maxPm25 = -1;

  data.forEach(d => {
    if(d.pm25 !== undefined) {
      sum += d.pm25;
      if(d.pm25 > maxPm25) maxPm25 = d.pm25;
    }
  });

  const avgPm25 = (sum / data.length).toFixed(1);

  // 根據數據動態決定文案策略
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

  // 將動態生成的文字塞入 HTML 中
  const marqueeEl = document.getElementById('dynamic-marquee');
  if (marqueeEl) {
    marqueeEl.innerHTML = `🟢 系統狀態：感測網連線正常 ｜ 最新資料同步：${latestTime} ｜ AI 洞察：${insight}`;
  }

  const summaryEl = document.getElementById('ai-summary-text');
  if (summaryEl) {
    summaryEl.innerHTML = aiSummary;
  }
}
// ==========================================

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
          title: { display: true, text: 'PM2.5 濃度時間趨勢', font: { size: 16 } },
          datalabels: { display: false } 
        },
        scales: {
          x: {
            ticks: {
              maxTicksLimit: isMobile ? 6 : 15,
              maxRotation: 45, 
              minRotation: 0
            }
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

// 抓取資料主函式 (PapaParse)
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
      
      // ★ 在資料讀取完畢後，執行我們的動態文字更新邏輯 ★
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