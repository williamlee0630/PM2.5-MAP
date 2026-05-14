// js/app.js
const sheetId = '1jcRopeeqnT786iB9m6Jd8oQH34S2AUE9Sp5-4eCgwYQ';
const csvUrl = `https://docs.google.com/spreadsheets/d/${sheetId}/export?format=csv`;

// 抓取資料的函式，加入 isAuto 參數來判斷是不是「背景自動更新」
function fetchData(isAuto = false) {
  // 如果是手動點擊更新，才顯示「讀取中...」，避免自動更新時文字一直閃爍
  if (!isAuto) {
    document.getElementById('data-status').innerText = '資料讀取中...';
  }

  Papa.parse(csvUrl, {
    download: true,
    header: true,
    dynamicTyping: true,
    complete: function(results) {
      const data = results.data.filter(row => row.latitude && row.longitude && row.pm25 !== undefined);
      
      document.getElementById('data-status').innerText = `共讀取 ${data.length} 筆資料 (最後更新: ${new Date().toLocaleTimeString()})`;

      const tableBody = document.getElementById('data-table-body');
      tableBody.innerHTML = '';
      
      // ★ 關鍵優化：在成功取得 Google Sheets 新資料後，才清空地圖上的舊標記
      clearMapMarkers();

      const reversedData = [...data].reverse();

      // ★ 修改點：加入了 index 參數
      reversedData.forEach(function(point, index) {
        // ★ 告訴地圖哪一筆是最新的 (index === 0 代表反轉後的第一筆，也就是最新資料)
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
      
      // 如果目前停留在地圖頁面，順便刷新一下地圖大小與縮放
      if (document.getElementById('view-map').classList.contains('active')) {
        refreshMapLayout();
      }
    },
    error: function(err) {
      console.error(err);
      // 如果自動更新失敗（例如網路突然斷線），不覆蓋畫面狀態，但如果是手動更新就顯示錯誤
      if (!isAuto) {
        document.getElementById('data-status').innerText = '讀取失敗，請確認 Google Sheets 權限';
        document.getElementById('data-status').style.color = 'red';
      }
    }
  });
}

// 給「更新數據」按鈕呼叫的函式 (手動更新)
function refreshData() {
  fetchData(false); // 傳入 false 代表這不是自動更新
}

// 網頁載入後執行
document.addEventListener('DOMContentLoaded', () => {
  // 1. 網頁剛打開時，先抓取第一次資料
  fetchData(false);
  
  // 2. 設定計時器 (setInterval)，每 10000 毫秒 (10秒) 自動在背景執行一次
  setInterval(() => {
    fetchData(true); // 傳入 true 代表是背景自動更新，靜默執行
  }, 10000);
});