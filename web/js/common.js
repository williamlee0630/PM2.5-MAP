// js/common.js
// ══════════════════════════════════════════════════════════════
// 所有頁面共用的工具函式與 UI 互動
// ══════════════════════════════════════════════════════════════

// ── PM2.5 顏色 / 狀態輔助函式（原 map.js，app.js 也用到）──────
function getColor(pm25) {
  if (pm25 < 15.5)  return '#00e400';
  if (pm25 < 35.5)  return '#ffff00';
  if (pm25 < 54.5)  return '#ff7e00';
  if (pm25 < 150.5) return '#ff0000';
  if (pm25 < 250.5) return '#8f3f97';
  return '#7e0023';
}

function getStatus(pm25) {
  if (pm25 < 15.5)  return '良好';
  if (pm25 < 35.5)  return '普通';
  if (pm25 < 54.5)  return '對敏感族群不健康';
  if (pm25 < 150.5) return '對所有族群不健康';
  if (pm25 < 250.5) return '非常不健康';
  return '危害';
}

// ── XSS 防護用工具函式（原 index.html 行內）───────────────────
function escapeHtml(str) {
  const div = document.createElement('div');
  div.appendChild(document.createTextNode(String(str ?? '')));
  return div.innerHTML;
}

// ── 側邊欄展開/收合（原 ui.js）─────────────────────────────────
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

// ── 頁面載入時初始化 ─────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  // 手機螢幕預設收縮側邊欄
  if (window.innerWidth <= 768) {
    const sidebar = document.getElementById('sidebar');
    if (sidebar) {
      sidebar.classList.add('collapsed');
    }
  }
});
