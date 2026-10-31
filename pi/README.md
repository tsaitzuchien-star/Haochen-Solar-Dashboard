# 樹莓派安裝步驟

樹莓派放在逆變器和電池旁邊，24 小時開著：每分鐘讀一次逆變器與 4 組電池 BMS，
上傳給網站「🔋 儲能」分頁即時顯示，有異常直接推播 LINE。

## 需要的東西

- Raspberry Pi 5（4GB）全配：主機、27W 電源、散熱器、外殼、32GB 以上 microSD
- USB 轉 RS485 轉接器 × 2（Waveshare 同款）
  - 轉接器 1 → 逆變器 **CN5: Computer**（第 1 腳 → B-、第 2 腳 → A+）
  - 轉接器 2 → 電池主機 **RS485C**（同樣第 1 腳 → B-、第 2 腳 → A+）
- 程式會自動分辨哪個轉接器接哪裡；電池那條**只聽不說**，絕不送出任何資料

## 1. 燒錄系統（在 Windows 筆電上）

1. 下載安裝 **Raspberry Pi Imager**：https://www.raspberrypi.com/software/
2. 裝置選 **Raspberry Pi 5**，系統選 **Raspberry Pi OS Lite (64-bit)**，儲存裝置選 microSD 卡
3. 按「下一步」→「**編輯設定**」：
   - 主機名稱：`haochen-pi`
   - 使用者名稱 / 密碼：自己設定（例如使用者 `pi`）
   - WiFi：家裡的 WiFi 名稱與密碼，國家選 `TW`
   - 「服務」分頁：**啟用 SSH**（使用密碼登入）
4. 寫入完成後，SD 卡插進樹莓派，接上兩個 USB 轉接器，插電開機，等 2 分鐘

## 2. 從筆電連進樹莓派

在 Windows「命令提示字元」輸入（`pi` 換成你設定的使用者名稱）：

```
ssh pi@haochen-pi.local
```

第一次會問 `Are you sure...`，輸入 `yes`，再輸入密碼。

## 3. 安裝程式

```
git clone https://github.com/tsaitzuchien-star/Haochen-Solar-Dashboard.git
cd Haochen-Solar-Dashboard
bash pi/install.sh
```

## 4. 設定金鑰

先準備兩把 GitHub 金鑰（https://github.com/settings/personal-access-tokens/new）：

| 用途 | 權限 |
|---|---|
| 樹莓派上傳用 | Account permissions → **Gists：Read and write** |
| 網站讀取用 | Account permissions → **Gists：Read-only** |

然後在樹莓派執行：

```
.venv/bin/python pi/solar_logger.py --setup
```

依提示貼上 LINE 的 Channel access token 和「上傳用」金鑰，程式會自動建立一個私人 Gist，
並印出要加到 Streamlit Cloud Secrets 的兩行（`LIVE_GIST_ID`、`GIST_TOKEN` 用「讀取用」金鑰）。

## 5. 測試與啟動

```
.venv/bin/python pi/solar_logger.py --once     # 讀一次，看資料是否正確
sudo systemctl start solar-logger              # 開始常駐（之後開機會自動啟動）
journalctl -u solar-logger -f                  # 即時看執行狀況（Ctrl+C 離開）
```

每天的記錄存在樹莓派的 `~/solar_logs/日期.csv`。

## 更新程式

```
cd ~/Haochen-Solar-Dashboard && git pull && sudo systemctl restart solar-logger
```
