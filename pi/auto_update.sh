#!/bin/bash
# 樹莓派自動更新：每 5 分鐘檢查 GitHub main 有沒有新版，有的話 git pull 並重新啟動 solar-logger，用 LINE 通知。
# 由 pi/install_autoupdate.sh 裝成 systemd timer (root 執行；git 用 repo 擁有者的身分跑)。
# 只做 fast-forward：樹莓派上有人手動改過檔案、或分支分岔時不會硬蓋，只記錄原因。
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
OWNER="$(stat -c %U "$REPO")"
LOG="$REPO/pi/auto_update.log"
as_owner() { runuser -u "$OWNER" -- "$@"; }
stamp() { date '+%m/%d %H:%M'; }
note() { echo "$(stamp) $*" >> "$LOG"; tail -n 50 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"; chown "$OWNER" "$LOG"; }

cd "$REPO" || exit 1
as_owner git fetch -q origin main || { note "抓不到 GitHub (網路?)"; exit 0; }
LOCAL=$(as_owner git rev-parse HEAD)
REMOTE=$(as_owner git rev-parse origin/main)
[ "$LOCAL" = "$REMOTE" ] && exit 0
if [ "$(as_owner git rev-parse --abbrev-ref HEAD)" != "main" ]; then note "不在 main 分支，不自動更新"; exit 0; fi
if ! as_owner git merge-base --is-ancestor HEAD origin/main; then note "本機和 GitHub 分岔了，不自動更新 (請 SSH 處理)"; exit 0; fi
CHANGED=$(as_owner git diff --name-only HEAD origin/main)
if ! as_owner git merge -q --ff-only origin/main; then note "更新失敗 (本機有改過檔案?)"; exit 0; fi
NEW=$(as_owner git log -1 --format='%h %s' | cut -c1-70)
# 只有樹莓派會用到的程式改了才重開 (網站程式 app.py 等不用)
if echo "$CHANGED" | grep -qE '^(pi/|tools/|solar_core.py|home_map.py)'; then
  systemctl restart solar-logger && note "已更新並重新啟動：$NEW" || note "已更新但重新啟動失敗：$NEW"
  MSG="🔄 樹莓派已自動更新並重新啟動：$NEW"
else
  note "已更新 (只有網站程式，不用重啟)：$NEW"
  MSG=""
fi
if [ -n "$MSG" ]; then
  as_owner "$REPO/.venv/bin/python" -c "
import json, sys, requests
cfg = json.load(open('$REPO/pi/config.json'))
t = cfg.get('line_token')
t and requests.post('https://api.line.me/v2/bot/message/broadcast', timeout=15,
                    headers={'Authorization': 'Bearer ' + t}, json={'messages': [{'type': 'text', 'text': sys.argv[1]}]})
" "$MSG" >/dev/null 2>&1 || true
fi
