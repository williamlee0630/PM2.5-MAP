# ═══════════════════════════════════════════════════════════════
# 【CELL 1】認證（每個 Colab session 執行一次）
# ═══════════════════════════════════════════════════════════════
from google.colab import auth
from google.auth import default
import gspread

auth.authenticate_user()
creds, _ = default()
gc = gspread.authorize(creds)

SHEET_ID        = '1jcRopeeqnT786iB9m6Jd8oQH34S2AUE9Sp5-4eCgwYQ'
RAW_SHEET_NAME  = 'Sheet1'        # 原始資料分頁
ARCH_SHEET_NAME = '歷史彙整'      # 壓縮後的歷史資料分頁

spreadsheet = gc.open_by_key(SHEET_ID)
raw_sheet   = spreadsheet.sheet1

print(f"✅ 已連線 → {spreadsheet.title}")
print(f"   原始資料：{raw_sheet.title}（共 {raw_sheet.row_count} 列）")


# ═══════════════════════════════════════════════════════════════
# 【CELL 2】取得或建立歷史彙整分頁
# ═══════════════════════════════════════════════════════════════
from gspread.exceptions import WorksheetNotFound

try:
    arch_sheet = spreadsheet.worksheet(ARCH_SHEET_NAME)
    print(f"✅ 已找到歷史彙整分頁（現有 {arch_sheet.row_count} 列）")
except WorksheetNotFound:
    arch_sheet = spreadsheet.add_worksheet(
        title=ARCH_SHEET_NAME, rows=10000, cols=8
    )
    # 寫入標題列
    arch_sheet.append_row([
        '小時區間', '平均 PM2.5', '最高 PM2.5', '最低 PM2.5',
        '代表緯度', '代表經度', '樣本數', '壓縮時間'
    ])
    print(f"✅ 已建立歷史彙整分頁")


# ═══════════════════════════════════════════════════════════════
# 【CELL 3】壓縮函式定義
# ═══════════════════════════════════════════════════════════════
import pandas as pd
from datetime import datetime, timedelta
import numpy as np

RETAIN_DAYS = 7   # 原始資料保留天數

def run_compression(dry_run=False):
    """
    把 RAW_SHEET 中 7 天以前的資料壓縮成每小時一筆，
    寫入 ARCH_SHEET 後從 RAW_SHEET 刪除。

    dry_run=True：只預覽，不實際寫入或刪除。
    """
    now        = datetime.now()
    cutoff     = now - timedelta(days=RETAIN_DAYS)
    run_time   = now.strftime('%Y-%m-%d %H:%M:%S')

    print(f"═══ 壓縮任務開始 {'（預覽模式）' if dry_run else ''} ═══════════════")
    print(f"  執行時間 : {run_time}")
    print(f"  保留門檻 : {cutoff.strftime('%Y-%m-%d %H:%M:%S')} 以後的資料")

    # ── 1. 讀取原始資料 ──────────────────────────────────────────
    raw_data = raw_sheet.get_all_records()
    if not raw_data:
        print("  原始資料為空，結束。")
        return

    df = pd.DataFrame(raw_data)

    # 確認欄位存在
    required = ['timestamp', 'pm25', 'latitude', 'longitude']
    missing  = [c for c in required if c not in df.columns]
    if missing:
        print(f"  ❌ 缺少欄位：{missing}，請確認 Sheet 標題列")
        return

    df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
    df = df.dropna(subset=['timestamp', 'pm25'])
    df['pm25']      = pd.to_numeric(df['pm25'],      errors='coerce')
    df['latitude']  = pd.to_numeric(df['latitude'],  errors='coerce')
    df['longitude'] = pd.to_numeric(df['longitude'], errors='coerce')
    df = df.dropna(subset=['pm25', 'latitude', 'longitude'])

    print(f"  原始有效資料：{len(df)} 筆")

    # ── 2. 切分：待壓縮 vs 保留 ──────────────────────────────────
    old_mask   = df['timestamp'] < cutoff
    df_old     = df[old_mask].copy()
    df_recent  = df[~old_mask].copy()

    print(f"  待壓縮（>{RETAIN_DAYS}天）：{len(df_old)} 筆")
    print(f"  保留（≤{RETAIN_DAYS}天） ：{len(df_recent)} 筆")

    if len(df_old) == 0:
        print("  無需壓縮的舊資料，結束。")
        return

    # ── 3. 取得歷史彙整已有的小時區間（避免重複寫入）───────────────
    existing_hours = set()
    arch_records   = arch_sheet.get_all_records()
    if arch_records:
        for rec in arch_records:
            if rec.get('小時區間'):
                existing_hours.add(rec['小時區間'])
    print(f"  歷史彙整已有 {len(existing_hours)} 個小時區間")

    # ── 4. 按小時分組壓縮 ────────────────────────────────────────
    df_old['hour_key'] = df_old['timestamp'].dt.strftime('%Y-%m-%d %H:00')

    new_arch_rows = []
    for hour_key, group in df_old.groupby('hour_key'):
        if hour_key in existing_hours:
            continue   # 已壓縮過，跳過

        avg_pm25  = round(float(group['pm25'].mean()), 2)
        max_pm25  = round(float(group['pm25'].max()),  2)
        min_pm25  = round(float(group['pm25'].min()),  2)
        rep_lat   = round(float(group['latitude'].mean()),  6)
        rep_lon   = round(float(group['longitude'].mean()), 6)
        count     = len(group)

        new_arch_rows.append([
            hour_key, avg_pm25, max_pm25, min_pm25,
            rep_lat, rep_lon, count, run_time
        ])

    print(f"  新增歷史彙整：{len(new_arch_rows)} 個小時區間")

    # ── 5. 預覽模式：只印出結果不執行 ──────────────────────────────
    if dry_run:
        print("\n  ── 預覽：前 5 筆新歷史彙整 ──")
        for row in new_arch_rows[:5]:
            print(f"    {row[0]}  avg={row[1]}  max={row[2]}  n={row[6]}")
        print(f"\n  預覽完成（dry_run=True，未實際寫入）")
        return

    # ── 6. 寫入歷史彙整 ───────────────────────────────────────────
    if new_arch_rows:
        arch_sheet.append_rows(new_arch_rows, value_input_option='USER_ENTERED')
        print(f"  ✅ 歷史彙整寫入完成")

    # ── 7. 用保留資料覆寫原始 Sheet（比逐列刪除更安全）─────────────
    # 策略：清空 Sheet → 重寫標題列 + 保留資料
    print(f"  正在覆寫原始 Sheet（保留 {len(df_recent)} 筆）...")

    # 取得原始標題列（保留欄位順序）
    header = raw_sheet.row_values(1)

    # 清空後重寫
    raw_sheet.clear()
    raw_sheet.append_row(header)

    if len(df_recent) > 0:
        # 還原成原始欄位順序
        df_recent['timestamp'] = df_recent['timestamp'].dt.strftime('%Y-%m-%d %H:%M:%S')
        rows_to_write = df_recent[header].values.tolist()
        raw_sheet.append_rows(rows_to_write, value_input_option='USER_ENTERED')

    print(f"  ✅ 原始 Sheet 覆寫完成")
    print(f"\n═══ 壓縮完成 ══════════════════════════════════════════")
    print(f"  刪除舊資料：{len(df_old)} 筆")
    print(f"  新增彙整：  {len(new_arch_rows)} 個小時區間")
    print(f"  保留原始：  {len(df_recent)} 筆")


# ═══════════════════════════════════════════════════════════════
# 【CELL 4】執行壓縮
# ═══════════════════════════════════════════════════════════════

# ★ 第一次執行建議先用 dry_run=True 預覽，確認無誤再改成 False
run_compression(dry_run=True)

# 確認結果正確後，取消下方的註解並執行
# run_compression(dry_run=False)


# ═══════════════════════════════════════════════════════════════
# 【CELL 5】（選用）查看歷史彙整現況
# ═══════════════════════════════════════════════════════════════
def show_archive_summary():
    records = arch_sheet.get_all_records()
    if not records:
        print("歷史彙整目前為空")
        return
    df_arch = pd.DataFrame(records)
    print(f"歷史彙整共 {len(df_arch)} 個小時區間")
    print(f"時間範圍：{df_arch['小時區間'].min()} ~ {df_arch['小時區間'].max()}")
    print(f"平均 PM2.5 總體均值：{round(df_arch['平均 PM2.5'].astype(float).mean(), 2)}")
    print(df_arch.tail(5).to_string(index=False))

# show_archive_summary()