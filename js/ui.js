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
}

// ★ 新增：專門給「查看分析圖表」按鈕呼叫的函式
function goToCharts() {
  const chartsMenuBtn = document.getElementById('nav-charts');
  switchTab('charts', chartsMenuBtn);
}