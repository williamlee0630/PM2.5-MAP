// js/ui.js

// ── Hash 路由防遞迴旗標 ──────────────────────────────────────────
let _suppressHashChange = false;

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

// 切換主畫面的頁籤 (Tabs)，同時更新 URL Hash 路由
function switchTab(tabId, clickedElement) {
  // 1. 隱藏所有內容區塊與取消選單反白狀態
  document.querySelectorAll('.view-section').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.menu-item').forEach(el => el.classList.remove('active'));
  
  // 2. 顯示被點擊的目標區塊 (加上防呆機制：確定找得到區塊才加 class)
  const targetView = document.getElementById('view-' + tabId);
  if (targetView) {
    targetView.classList.add('active');
  }

  // 3. 如果沒有傳入按鈕元素，透過 data-tab 屬性自動查找對應的側邊欄按鈕
  if (!clickedElement) {
    clickedElement = document.querySelector(`.menu-item[data-tab="${tabId}"]`);
  }

  // 4. 將點擊的按鈕設為 active (加上防呆機制：確定有傳入按鈕元素才執行)
  if (clickedElement) {
    clickedElement.classList.add('active');
  }

  // 5. 如果切換到地圖頁，確保地圖尺寸正確載入
  if (tabId === 'map' && typeof refreshMapLayout === 'function') {
    refreshMapLayout();
  }

  // 6. 手機版優化：點擊選單後自動收合側邊欄，讓出畫面空間
  if (window.innerWidth <= 768) {
    const sidebar = document.getElementById('sidebar');
    if (sidebar && !sidebar.classList.contains('collapsed')) {
      sidebar.classList.add('collapsed');
    }
  }

  // 7. 更新 URL Hash 路由（避免觸發 hashchange 的遞迴）
  const newHash = '#/' + tabId;
  if (location.hash !== newHash) {
    _suppressHashChange = true;
    location.hash = newHash;
  }
}

// 專門給「查看分析圖表」按鈕呼叫的跳轉函式
function goToCharts() {
  switchTab('charts');
}

// ── Hash 路由：解析目前 hash 並切換至對應頁面 ────────────────────
function handleHashRoute() {
  const hash = location.hash.replace('#/', '') || 'home';
  const validTabs = ['home', 'routing', 'news', 'map', 'data', 'charts', 'settings'];
  const tabId = validTabs.includes(hash) ? hash : 'home';
  switchTab(tabId);
}

// ── Hash 路由：監聽瀏覽器「返回」與「前進」按鈕 ─────────────────
window.addEventListener('hashchange', () => {
  if (_suppressHashChange) {
    _suppressHashChange = false;
    return;
  }
  handleHashRoute();
});

// 網頁剛載入時的初始化
document.addEventListener('DOMContentLoaded', () => {
  // 手機螢幕預設收縮側邊欄
  if (window.innerWidth <= 768) {
    const sidebar = document.getElementById('sidebar');
    if (sidebar) {
      sidebar.classList.add('collapsed');
    }
  }

  // 讀取 URL Hash，直接導向對應頁面（支援網址分享與書籤）
  if (location.hash && location.hash.startsWith('#/')) {
    handleHashRoute();
  } else {
    // 沒有 hash 時，預設為首頁並寫入 hash
    _suppressHashChange = true;
    location.hash = '#/home';
  }
});