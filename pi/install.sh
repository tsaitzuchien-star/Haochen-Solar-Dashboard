#!/bin/bash
# 樹莓派一鍵安裝：Python 套件、開機自動執行的服務
set -e
cd "$(dirname "$0")/.."
REPO=$(pwd)
echo "== 安裝系統套件 =="
sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv python3-pip git
echo "== 建立 Python 環境 =="
python3 -m venv "$REPO/.venv"
"$REPO/.venv/bin/pip" install -q --upgrade pip
"$REPO/.venv/bin/pip" install -q pyserial requests pandas numpy cryptography zeroconf
echo "== 讓目前使用者可以讀 USB 序列埠 =="
sudo usermod -aG dialout "$USER"
echo "== 設定開機自動執行 =="
sudo tee /etc/systemd/system/solar-logger.service > /dev/null <<UNIT
[Unit]
Description=澔宸小窩 太陽能 / 電池即時記錄
After=network-online.target
Wants=network-online.target

[Service]
User=$USER
WorkingDirectory=$REPO
ExecStart=$REPO/.venv/bin/python $REPO/pi/solar_logger.py
Restart=always
RestartSec=20

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable solar-logger
echo
echo "✅ 安裝完成。接下來："
echo "  1. $REPO/.venv/bin/python pi/solar_logger.py --setup   （輸入 LINE 與 GitHub 金鑰）"
echo "  2. $REPO/.venv/bin/python pi/solar_logger.py --once    （測試讀一次）"
echo "  3. sudo systemctl start solar-logger                   （開始常駐執行）"
echo "  看執行狀況：journalctl -u solar-logger -f"
