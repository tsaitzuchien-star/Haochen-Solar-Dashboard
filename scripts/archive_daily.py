"""每日存檔：把 Growatt 的 5 分鐘明細與每日摘要存進 repo (由 GitHub Actions 每天執行)。

Growatt 只保留最近 3 個月的明細，所以第一次執行會往回補到 89 天前，之後每天補上缺的日子。
遇到限流 (FREQUENTLY_ACCESS) 會等待重試，仍失敗就先停止，已存的資料保留，下次再接著補。

用法:
    GROWATT_TOKEN=xxx python scripts/archive_daily.py
輸出:
    data/daily.csv                  每日摘要 (一天一列)
    data/raw/YYYY/MM/YYYY-MM-DD.csv.gz  當天 5 分鐘明細
"""
import csv
import datetime as dt
import os
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from solar_core import DAILY_COLUMNS, TW_TZ, daily_summary, rows_to_curve  # noqa: E402

TOKEN = os.environ.get("GROWATT_TOKEN")
DEVICE_SN = os.environ.get("GROWATT_DEVICE_SN", "KQQ0N6200N")
API = "https://openapi.growatt.com/v4/new-api/queryHistoricalData"
ROOT = Path(__file__).resolve().parent.parent / "data"
DAILY = ROOT / "daily.csv"
PAUSE = 6
MAX_DAYS_BACK = 89


class RateLimited(Exception):
    pass


def fetch_day(day):
    for attempt in range(4):
        r = requests.post(API, headers={"token": TOKEN}, timeout=60,
                          data={"deviceSn": DEVICE_SN, "deviceType": "sph-s", "date": day.isoformat()})
        r.raise_for_status()
        d = r.json()
        time.sleep(PAUSE)
        if d.get("code") == 0:
            return (d.get("data") or {}).get("datas") or []
        if d.get("message") == "DATE_WITHIN_3_MONTHS":
            return None
        if d.get("message") == "TOKEN_IS_INVALID":
            sys.exit("Token 無效，請更新 GitHub repo 的 GROWATT_TOKEN secret")
        time.sleep(60 * (attempt + 1))  # 限流：等待後重試
    raise RateLimited(f"{day}: {d}")


def load_daily():
    if not DAILY.exists():
        return {}
    with open(DAILY, encoding="utf-8") as f:
        return {row["date"]: row for row in csv.DictReader(f)}


def save_daily(rows):
    ROOT.mkdir(parents=True, exist_ok=True)
    with open(DAILY, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=DAILY_COLUMNS)
        w.writeheader()
        for d in sorted(rows):
            w.writerow({k: rows[d].get(k, "") for k in DAILY_COLUMNS})


def main():
    if not TOKEN:
        sys.exit("請設定環境變數 GROWATT_TOKEN")
    today = dt.datetime.now(TW_TZ).date()
    rows = load_daily()
    # 只存完整的日子 (到昨天為止)；資料點太少 (當天斷線補傳中) 的日子會重抓
    todo = [today - dt.timedelta(days=i) for i in range(MAX_DAYS_BACK, 0, -1)
            if (r := rows.get((today - dt.timedelta(days=i)).isoformat())) is None or int(r.get("points") or 0) < 200]
    print(f"需要補抓 {len(todo)} 天")
    saved = 0
    try:
        for day in todo:
            data = fetch_day(day)
            if not data:
                print(f"{day}: 無資料（採集器離線或超過保存期限）")
                continue
            curve = rows_to_curve(data)
            raw = ROOT / "raw" / f"{day:%Y}" / f"{day:%m}" / f"{day}.csv.gz"
            raw.parent.mkdir(parents=True, exist_ok=True)
            curve.to_csv(raw, index=False, compression="gzip", float_format="%.3f")
            rows[day.isoformat()] = daily_summary(curve)
            saved += 1
            print(f"{day}: {len(curve)} 筆，發電 {rows[day.isoformat()]['pv']} kWh")
            if saved % 10 == 0:
                save_daily(rows)
    except RateLimited as e:
        print(f"Growatt 限流，先停止，下次接著補：{e}")
    save_daily(rows)
    print(f"完成：新增 / 更新 {saved} 天，共 {len(rows)} 天")


if __name__ == "__main__":
    main()
