// js/ui.js
function toggleSidebar() {
  const sidebar = document.getElementById('sidebar');
  sidebar.classList.toggle('collapsed');
  
  if (document.getElementById('view-map').classList.contains('active')) {
    setTimeout(() => { refreshMapLayout(); }, 300);
  }
}

function switchTab(tabId, clickedElement) {
  document.querySelectorAll('.view-section').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.menu-item').forEach(el => el.classList.remove('active'));
  
  document.getElementById('view-' + tabId).classList.add('active');
  clickedElement.classList.add('active');

  if (tabId === 'map') {
    refreshMapLayout();
  }

  // ★ 新增：如果是手機版，點擊選單後自動收合側邊欄 ★
  if (window.innerWidth <= 768) {
    const sidebar = document.getElementById('sidebar');
    if (!sidebar.classList.contains('collapsed')) {
      sidebar.classList.add('collapsed');
    }
  }
}

// ★ 新增：專門給「查看分析圖表」按鈕呼叫的函式
function goToCharts() {
  const chartsMenuBtn = document.getElementById('nav-charts');
  switchTab('charts', chartsMenuBtn);
}

// ★ 新增：網頁剛載入時，如果是手機螢幕，預設將側邊欄收縮 ★
document.addEventListener('DOMContentLoaded', () => {
  if (window.innerWidth <= 768) {
    document.getElementById('sidebar').classList.add('collapsed');
  }
});