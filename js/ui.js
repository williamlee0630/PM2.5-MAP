// js/ui.js
// 處理側邊欄與畫面切換邏輯

// 收合/展開側邊欄的功能
function toggleSidebar() {
  const sidebar = document.getElementById('sidebar');
  sidebar.classList.toggle('collapsed');
  
  // 如果地圖目前是開啟狀態，側邊欄收合會改變主畫面寬度，需要請地圖重新計算大小
  if (document.getElementById('view-map').classList.contains('active')) {
    // 給予一點延遲，等待 CSS transition 動畫跑完再計算
    setTimeout(() => {
      refreshMapLayout();
    }, 300);
  }
}

// 切換主畫面的功能
function switchTab(tabId, clickedElement) {
  // 1. 隱藏所有視圖
  document.querySelectorAll('.view-section').forEach(el => {
    el.classList.remove('active');
  });
  
  // 2. 取消所有側邊欄項目的 active 狀態
  document.querySelectorAll('.menu-item').forEach(el => {
    el.classList.remove('active');
  });
  
  // 3. 顯示目標視圖
  document.getElementById('view-' + tabId).classList.add('active');
  
  // 4. 將點擊的選單項目加上 active 樣式 (淺藍底色)
  clickedElement.classList.add('active');

  // 5. 如果切換到地圖頁面，呼叫地圖重繪的函式
  if (tabId === 'map') {
    refreshMapLayout();
  }
}