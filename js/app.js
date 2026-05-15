// js/app.js
const sheetId = '1jcRopeeqnT786iB9m6Jd8oQH34S2AUE9Sp5-4eCgwYQ';
const csvUrl = `https://docs.google.com/spreadsheets/d/${sheetId}/export?format=csv`;

// 用來儲存圖表的變數
let trendChartInstance = null;
let pieChartInstance = null;

// ★ 新增：畫圖表的專用函式
function renderCharts(data) {
  // 準備資料 (依舊到新排列)
  const labels = data.map(d => d.timestamp ? d.timestamp.split(' ')[1] || d.timestamp : '');
  const pm25Values = data.map(d => d.pm25);
  const statusCounts = { '良好': 0, '普通': 0, '對敏感族群不健康': 0, '對所有族群不健康': 0, '非常不健康': 0, '危害': 0 };
  const statusColors = ['#00e400', '#ffff00', '#ff7e00', '#ff0000', '#8f3f97', '#7e0023'];

  data.forEach(d => {
    const status = getStatus(d.pm25);
    if (statusCounts[status] !== undefined) statusCounts[status]++;
  });

  // 如果原本已經有圖，先清掉避免重疊
  if (trendChartInstance) trendChartInstance.destroy();
  if (pieChartInstance) pieChartInstance.destroy();

  // 畫折線圖
  const ctxTrend = document.getElementById('pm25TrendChart');
  if (ctxTrend) {
    trendChartInstance = new Chart(ctxTrend.getContext('2d'), {
      type: 'line',
      data: {
        labels: labels,
        datasets: [{ label: 'PM2.5 濃度 (µg/m³)', data: pm25Values, borderColor: '#3498db', backgroundColor: 'rgba(52, 152, 219, 0.2)', borderWidth: 2, tension: 0.3, fill: true }]
      },
      options: { responsive: true, maintainAspectRatio: false, plugins: { title: { display: true, text: 'PM2.5 濃度時間趨勢', font: { size: 16 } } } }
    });
  }

  // 畫圓餅圖
  const ctxPie = document.getElementById('statusPieChart');
  if (ctxPie) {
    pieChartInstance = new Chart(ctxPie.getContext('2d'), {
      type: 'doughnut',
      data: {
        labels: Object.keys(statusCounts),
        datasets: [{ data: Object.values(statusCounts), backgroundColor: statusColors, borderWidth: 1 }]
      },
      options: { responsive: true, maintainAspectRatio: false, plugins: { title: { display: true, text: '空氣品質狀態分佈', font: { size: 16 } }, legend: { position: 'right' } } }
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
      const data = results.data.filter(row => row.latitude && row.longitude && row.pm25 !== undefined);
      document.getElementById('data-status').innerText = `共讀取 ${data.length} 筆資料 (最後更新: ${new Date().toLocaleTimeString()})`;

      const tableBody = document.getElementById('data-table-body');
      tableBody.innerHTML = '';
      
      clearMapMarkers();
      
      // ★ 呼叫畫圖表函式
      renderCharts(data);

      const reversedData = [...data].reverse();
      reversedData.forEach(function(point, index) {
        addMarkerToMap(point, index === 0); // 包含您剛才加好的地圖跟隨邏輯
        
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