// js/app.js
const sheetId = '1jcRopeeqnT786iB9m6Jd8oQH34S2AUE9Sp5-4eCgwYQ';
const csvUrl = `https://docs.google.com/spreadsheets/d/${sheetId}/export?format=csv`;

let trendChartInstance = null;
let pieChartInstance = null;
let globalData = []; // ★ 新增：用來暫存抓到的資料，方便篩選器直接使用

// 畫圖表函式 (現在可以直接呼叫它來刷新圖表)
function renderCharts() {
  if (globalData.length === 0) return;

  // --- 1. 取得使用者的篩選條件 ---
  const filterElement = document.getElementById('pm25TrendFilter');
  const filterValue = filterElement ? filterElement.value : 'all';
  
  // 根據篩選條件過濾折線圖的資料
  let trendData = globalData;
  if (filterValue === 'warning') {
    trendData = globalData.filter(d => d.pm25 >= 35.5);
  } else if (filterValue === 'danger') {
    trendData = globalData.filter(d => d.pm25 >= 54.5);
  }

  // 準備折線圖資料
  const labels = trendData.map(d => d.timestamp ? d.timestamp.split(' ')[1] || d.timestamp : '');
  const pm25Values = trendData.map(d => d.pm25);

  // 準備圓餅圖資料 (圓餅圖不受篩選影響，永遠顯示整體比例)
  const statusCounts = { '良好': 0, '普通': 0, '對敏感族群不健康': 0, '對所有族群不健康': 0, '非常不健康': 0, '危害': 0 };
  const statusColors = ['#00e400', '#ffff00', '#ff7e00', '#ff0000', '#8f3f97', '#7e0023'];
  globalData.forEach(d => {
    const status = getStatus(d.pm25);
    if (statusCounts[status] !== undefined) statusCounts[status]++;
  });

  if (trendChartInstance) trendChartInstance.destroy();
  if (pieChartInstance) pieChartInstance.destroy();

  // ★ 判斷是否為手機螢幕，用來調整圖表顯示細節
  const isMobile = window.innerWidth <= 768;

  // --- 2. 畫折線圖 (加入手機版優化參數) ---
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
          borderWidth: isMobile ? 1.5 : 2, // 手機版線條稍微變細
          pointRadius: isMobile ? 2 : 4,   // ★ 優化：手機版的資料點變小，避免擠成一團
          tension: 0.3, 
          fill: true 
        }]
      },
      options: { 
        responsive: true, 
        maintainAspectRatio: false, 
        plugins: { 
          title: { display: true, text: 'PM2.5 濃度時間趨勢', font: { size: 16 } } 
        },
        scales: {
          x: {
            ticks: {
              // ★ 核心優化：限制 X 軸標籤數量，手機最多顯示 6 個時間，電腦顯示 15 個
              maxTicksLimit: isMobile ? 6 : 15,
              maxRotation: 45, // 允許標籤稍微傾斜
              minRotation: 0
            }
          }
        }
      }
    });
  }

  // --- 3. 畫圓餅圖 ---
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
          legend: { position: isMobile ? 'bottom' : 'right' } // ★ 優化：手機版的圓餅圖圖例改放下方，才不會壓縮圓形空間
        } 
      }
    });
  }
}

// 抓取資料主函式
function fetchData(isAuto = false) {
  if (!isAuto) document.getElementById('data-status').innerText = '資料讀取中...';

  Papa.parse(csvUrl, {
    download: true,
    header: true,
    dynamicTyping: true,
    complete: function(results) {
      // 過濾並存入全域變數
      globalData = results.data.filter(row => row.latitude && row.longitude && row.pm25 !== undefined);
      
      document.getElementById('data-status').innerText = `共讀取 ${globalData.length} 筆資料 (最後更新: ${new Date().toLocaleTimeString()})`;

      const tableBody = document.getElementById('data-table-body');
      tableBody.innerHTML = '';
      
      clearMapMarkers();
      
      // 呼叫畫圖表函式
      renderCharts();

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