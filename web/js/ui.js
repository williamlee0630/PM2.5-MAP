// js/ui.js

// 切換側邊欄展開/收合
function toggleSidebar() {
  const sidebar = document.getElementById('sidebar');
  sidebar.classList.toggle('collapsed');
  
  // 如果當前在看地圖，延遲一點點時間讓地圖重新計算尺寸，避免破圖
  if (document.getElementById('view-map') && document.getElementById('view-map').classList.contains('active')) {
    setTimeout(() => { 
      if (typeof refreshMapLayout === 'function') refreshMapLayout(); 
    }, 300);
  }
}

// 切換主畫面的頁籤 (Tabs)
function switchTab(tabId, clickedElement) {
  // 1. 隱藏所有內容區塊與取消選單反白狀態
  document.querySelectorAll('.view-section').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.menu-item').forEach(el => el.classList.remove('active'));
  
  // 2. 顯示被點擊的目標區塊 (加上防呆機制：確定找得到區塊才加 class)
  const targetView = document.getElementById('view-' + tabId);
  if (targetView) {
    targetView.classList.add('active');
  }

  // 3. 將點擊的按鈕設為 active (加上防呆機制：確定有傳入按鈕元素才執行)
  if (clickedElement) {
    clickedElement.classList.add('active');
  }

  // 4. 如果切換到地圖頁，確保地圖尺寸正確載入
  if (tabId === 'map' && typeof refreshMapLayout === 'function') {
    refreshMapLayout();
  }

  // 5. 手機版優化：點擊選單後自動收合側邊欄，讓出畫面空間
  if (window.innerWidth <= 768) {
    const sidebar = document.getElementById('sidebar');
    if (sidebar && !sidebar.classList.contains('collapsed')) {
      sidebar.classList.add('collapsed');
    }
  }
}

// 專門給「查看分析圖表」按鈕呼叫的跳轉函式
function goToCharts() {
  const chartsMenuBtn = document.getElementById('nav-charts');
  if (chartsMenuBtn) {
    switchTab('charts', chartsMenuBtn);
  }
}

// 網頁剛載入時，如果是手機螢幕，預設將側邊欄收縮
document.addEventListener('DOMContentLoaded', () => {
  if (window.innerWidth <= 768) {
    const sidebar = document.getElementById('sidebar');
    if (sidebar) {
      sidebar.classList.add('collapsed');
    }
  }
});