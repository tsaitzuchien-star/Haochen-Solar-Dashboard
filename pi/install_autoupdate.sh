#!/bin/bash
# 一次性安裝：讓樹莓派每 5 分鐘自動從 GitHub main 更新 (之後不用再 SSH 進來 git pull / restart)。
#   sudo bash pi/install_autoupdate.sh
# 停用：sudo systemctl disable --now solar-autoupdate.timer
set -e
REPO="$(cd "$(dirname "$0")/.." && pwd)"
chmod +x "$REPO/pi/auto_update.sh"
tee /etc/systemd/system/solar-autoupdate.service > /dev/null <<UNIT
[Unit]
Description=澔宸小窩 樹莓派程式自動更新
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/bin/bash $REPO/pi/auto_update.sh
UNIT
tee /etc/systemd/system/solar-autoupdate.timer > /dev/null <<UNIT
[Unit]
Description=每 5 分鐘檢查樹莓派程式更新

[Timer]
OnBootSec=3min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now solar-autoupdate.timer
echo "$(date '+%m/%d %H:%M') 自動更新已啟用 (每 5 分鐘)" >> "$REPO/pi/auto_update.log"
chown "$(stat -c %U "$REPO")" "$REPO/pi/auto_update.log"
echo "✅ 已啟用：每 5 分鐘自動檢查 GitHub main，有新版就更新並重新啟動 solar-logger (會用 LINE 通知)"
systemctl list-timers solar-autoupdate.timer --no-pager
