"""每日存檔：每天凌晨由 GitHub Actions 執行，把前一天的「每日摘要」寫進 data/daily.csv，並記下今天的發電預測。

為了不觸發 Growatt 限流，一次執行最多查 MAX_FETCH 天 (平常只有昨天 1 次)：
資料不完整的日子 (筆數不到 270，例如採集器斷線或手動匯入的半天資料) 會在之後幾天陸續補齊；Growatt 只保留約 3 個月。
repo 是公開的，所以只存每日總量，不存 5 分鐘明細 (避免透露家中作息)。

用法：GROWATT_TOKEN=xxx python scripts/archive_daily.py
"""
import csv
import datetime as dt
import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import pv_forecast  # noqa: E402
from solar_core import DAILY_COLUMNS, TW_TZ, daily_summary, rows_to_curve  # noqa: E402

# 從 Streamlit Secrets 複製時常會連引號或空白一起貼上，先去掉
TOKEN = (os.environ.get("GROWATT_TOKEN") or "").strip().strip('"').strip("'").strip() or None
DEVICE_SN = os.environ.get("GROWATT_DEVICE_SN", "KQQ0N6200N")
API = "https://openapi.growatt.com/v4/new-api/queryHistoricalData"
DAILY = ROOT / "data" / "daily.csv"
FORECAST = ROOT / "data" / "forecast.csv"
FORECAST_COLUMNS = ["date", "made_at", "pv", "pv1", "pv3", "ghi", "cloud", "model"]
MAX_FETCH = 3          # 每次最多查幾天
KEEP_DAYS = 85         # Growatt 保留約 3 個月
COMPLETE = 270         # 一天 288 筆，少於這個數就視為不完整


def read_csv(path):
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return {row["date"]: row for row in csv.DictReader(f)}


def write_csv(path, rows, columns):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        for d in sorted(rows):
            w.writerow({k: rows[d].get(k, "") for k in columns})


def fetch_day(day):
    r = requests.post(API, headers={"token": TOKEN}, timeout=60,
                      data={"deviceSn": DEVICE_SN, "deviceType": "sph-s", "date": day.isoformat()})
    r.raise_for_status()
    d = r.json()
    if d.get("code") != 0:
        raise RuntimeError(d.get("message"))
    return (d.get("data") or {}).get("datas") or []


def main():
    today = dt.datetime.now(TW_TZ).date()
    rows = read_csv(DAILY)
    failed = False

    if TOKEN:
        todo = [today - dt.timedelta(days=i) for i in range(1, KEEP_DAYS)
                if int((rows.get((today - dt.timedelta(days=i)).isoformat()) or {}).get("points") or 0) < COMPLETE]
        for n, day in enumerate(todo[:MAX_FETCH]):     # 由近到遠，先存昨天
            if n:
                time.sleep(10)
            try:
                data = fetch_day(day)
            except Exception as e:
                print(f"::warning::{day}: Growatt 查詢失敗 ({e})，下次再試")
                failed = failed or any(k in str(e) for k in ("TOKEN", "PERMISSION"))
                if any(k in str(e) for k in ("TOKEN", "PERMISSION")):
                    print("::error::Growatt 拒絕這組 Token：請確認 GitHub Secret 的 GROWATT_TOKEN 與 Streamlit 的完全相同 (只貼引號裡的那串)")
                    break
                continue
            if not data:
                print(f"{day}: 無資料")
                continue
            new = daily_summary(rows_to_curve(data))
            old = rows.get(day.isoformat())
            if old is None or int(old.get("points") or 0) <= new["points"]:
                rows[day.isoformat()] = new
            print(f"{day}: {new['points']} 筆，發電 {new['pv']} kWh")
        write_csv(DAILY, rows, DAILY_COLUMNS)
    else:
        print("::error::沒有 GROWATT_TOKEN：請到 GitHub → Settings → Secrets and variables → Actions 新增 GROWATT_TOKEN")
        failed = True

    # 記下今天的發電預測，之後在趨勢分頁比對預測準確度
    try:
        _, days = pv_forecast.forecast()
        s = days.get(today)
        if s:
            fc = read_csv(FORECAST)
            fc[today.isoformat()] = {"date": today.isoformat(), "made_at": dt.datetime.now(TW_TZ).strftime("%Y-%m-%d %H:%M"),
                                     "pv": round(s["pv"], 1), "pv1": round(s["PV1"], 1), "pv3": round(s["PV3"], 1),
                                     "ghi": round(s["ghi"], 2), "cloud": round(s["cloud"]), "model": pv_forecast.MODEL_VERSION}
            write_csv(FORECAST, fc, FORECAST_COLUMNS)
            print(f"今天預測 {s['pv']:.1f} kWh")
    except Exception as e:
        print(f"::warning::預測失敗：{e}")
    if failed:
        sys.exit(1)   # 讓 GitHub 顯示紅色叉叉提醒 (已存的資料仍會在下一步提交)


if __name__ == "__main__":
    main()
