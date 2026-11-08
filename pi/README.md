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

## 澔宸 AI 智慧小窩（3D 房子顯示家電狀態）

樹莓派每 60 秒用**小米雲端**讀全部米家裝置（燈、冷氣、窗簾、鐵捲門…），每 5 分鐘讀 eWeLink 裝置，
跟著原本每分鐘的上傳一起送到網站（不會增加 Gist 上傳次數）。**只讀取，不會控制任何裝置。**

全部自動，不用輸入密碼：
1. 更新程式並重新啟動服務後，樹莓派會用 LINE 傳兩則訊息給你：
   - **家人專用連結**：打開就能看到 3D 小窩裡全部燈與家電的狀態，請加入書籤，不要分享給外人。
     沒有這個連結的訪客只看得到機房冷氣、熱水器與太陽能。
   - **小米登入 QR code**：用電腦打開連結，再用米家 App 右上角「+」→「掃一掃」掃描（只要做一次）。
2. 之後小米登入會自動續期；萬一失效，樹莓派會再用 LINE 傳 QR code（12 小時最多一次）。

檢查與除錯（SSH 進樹莓派）：
```bash
cd ~/Haochen-Solar-Dashboard
.venv/bin/python tools/mi_cloud.py list     # 全部裝置、房間、對應到 3D 圖的哪個位置
.venv/bin/python tools/mi_cloud.py status   # 讀一次所有裝置的開關狀態
.venv/bin/python tools/mi_cloud.py login    # 想手動登入小米時用
```
- 3D 圖上某個位置對錯或顯示「米家裡找不到」：多半是米家裡的名稱不同，把 `list` 的結果給 Claude，改 `home_map.py` 的規則就好。
- 想換家人連結（例如不小心傳給別人）：刪掉 `pi/home_key.json` 再 `sudo systemctl restart solar-logger`，會用 LINE 傳新的連結，舊連結失效。
- 想關掉這個功能：在 `pi/config.json` 加 `"home_watch": false`。
- 金鑰檔 `pi/mi_cloud.json`、`pi/home_key.json` 只存在樹莓派上（權限 600），不會上傳到 GitHub。

## 自動更新
已用 `sudo bash pi/install_autoupdate.sh` 安裝後，樹莓派每 5 分鐘自動檢查 GitHub main：
- 有新版就自動 `git pull`；改到樹莓派程式 (pi/、tools/ 等) 才重新啟動 solar-logger，並用 LINE 通知。
- 只改網站或說明檔時不重啟。
- 更新紀錄看網站「🍓 樹莓派」分頁的「🩺 診斷資訊」(家人連結才看得到)，或 `cat pi/auto_update.log`。
- 停用：`sudo systemctl disable --now solar-autoupdate.timer`
