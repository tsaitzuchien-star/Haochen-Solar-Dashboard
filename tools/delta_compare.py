"""比較每天「放電時的組內壓差」：螺絲鬆的那顆電芯，大電流放電時電壓會掉得比較多，壓差就會變大。
用來看鎖螺絲 / 清潔端子前後有沒有改善。只讀樹莓派的 ~/solar_logs/日期.csv，不改任何東西。

用法 (樹莓派)：
  .venv/bin/python tools/delta_compare.py            # 最近 10 天
  .venv/bin/python tools/delta_compare.py 2026-11-13 # 指定「改變的那一天」，前後分開平均
"""
import csv
import glob
import os
import statistics as st
import sys

LOG_DIR = os.path.expanduser("~/solar_logs")


def load(fn):
    out = {n: [] for n in range(4)}
    with open(fn, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            for n in range(4):
                try:
                    a = float(row[f"#{n}電流A"])
                    lo, hi = float(row[f"#{n}最低V"]), float(row[f"#{n}最高V"])
                except (KeyError, ValueError):
                    continue
                # 只看中段電量、放電 8–40 A：避開快充滿 / 快放空時電芯本來就會分開的區段
                if -40 <= a <= -8 and 3.20 <= (lo + hi) / 2 <= 3.33:
                    out[n].append(((hi - lo) * 1000, -a))
    return out


def main():
    files = sorted(f for f in glob.glob(os.path.join(LOG_DIR, "20*.csv")))[-10:]
    if not files:
        sys.exit(f"{LOG_DIR} 裡沒有記錄檔")
    cut = sys.argv[1] if len(sys.argv) > 1 else None
    print("放電 8–40 A、電芯 3.20–3.33 V 時的組內壓差 (mV) 與「每 10 A 增加多少 mV」(越小越好)\n")
    print(f"{'日期':12s}" + "".join(f"  #{n} 壓差   每10A" for n in range(4)) + "   筆數")
    groups = {"前": {n: [] for n in range(4)}, "後": {n: [] for n in range(4)}}
    for fn in files:
        day = os.path.basename(fn)[:10]
        d = load(fn)
        cells, cnt = [], 0
        for n in range(4):
            pts = d[n]
            cnt = max(cnt, len(pts))
            if len(pts) >= 5:
                dm = st.median(p[0] for p in pts)
                # 壓差對電流的斜率 (簡單線性回歸)：連接電阻差異的指標
                xs, ys = [p[1] for p in pts], [p[0] for p in pts]
                mx, my = st.mean(xs), st.mean(ys)
                vx = sum((x - mx) ** 2 for x in xs)
                k = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / vx * 10 if vx > 1 else float("nan")
                cells.append(f"  {dm:6.1f}  {k:6.2f}")
                if cut:
                    groups["前" if day < cut else "後"][n].append(dm)
            else:
                cells.append(f"  {'—':>6s}  {'—':>6s}")
        print(f"{day:12s}" + "".join(cells) + f"   {cnt}")
    if cut:
        print(f"\n以 {cut} 為界的平均壓差 (mV)：")
        for n in range(4):
            b, a = groups["前"][n], groups["後"][n]
            if b and a:
                print(f"  #{n}：之前 {st.mean(b):.1f} → 之後 {st.mean(a):.1f}（{st.mean(a) - st.mean(b):+.1f}）")


if __name__ == "__main__":
    main()
