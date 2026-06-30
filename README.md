# 雷電模擬器手遊自動化測試系統

搭配雷電模擬器 9（ldconsole + ADB），用「上傳錄影 → AI 產生腳本 → 跨解析度重複執行 → 成功率報告」的流程，自動化驗證手遊流程。

## 特色

- **跨解析度**：腳本座標一律正規化 (0~1)，同一份腳本可在 `1280x720 / 1920x1080 / 960x540` 等任意解析度執行。
- **圖像斷言**：OpenCV **多尺度模板比對**，模板圖在不同解析度下自動縮放比對，不必為每個解析度各做一份圖。
- **N 次重複**：每個解析度重複執行 N 次，逐步截圖。
- **成功率報告**：輸出 `report.json` 與自包含 `report.html`（含各解析度成功率與每步截圖縮圖）。

## 安裝

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
```

確認 `config/settings.yaml` 中的路徑與 `package_name`：

```yaml
ldplayer:
  console_path: "C:/LDPlayer/LDPlayer9/ldconsole.exe"
  adb_path:     "C:/LDPlayer/LDPlayer9/adb.exe"
  instance_index: 0
test:
  package_name: "com.example.game"   # ← 改成你的遊戲套件名
  repeat: 10
  resolutions: [...]
```

> 查套件名：`C:/LDPlayer/LDPlayer9/adb.exe -s 127.0.0.1:5555 shell pm list packages | findstr 關鍵字`

## 完整流程

### 1. 確認環境

```powershell
py run.py devices
```

列出雷電實例與 adb 裝置，確認 `instance_index` 對得上。

### 2. 上傳錄影並抽幀

影片來源資料夾預設指向雷電內建錄影輸出：`C:/LDPlayer/LDPlayer9/vms/video`
（可在 `config/settings.yaml` 的 `paths.video_source` 修改）。

**自動監看（推薦）**——掃描來源資料夾，對「尚無對應腳本」的新影片自動抽幀：

```powershell
py run.py watch            # 掃一次
py run.py watch --watch    # 常駐監看（每 5 秒輪詢，Ctrl+C 結束）
py run.py watch --force    # 即使抽過幀也重抽
```

抽完會列出「待分析影片」清單，每個影片對應 `scripts/<影片名>.yaml`。

**手動單檔抽幀**：

```powershell
py run.py extract C:\LDPlayer\LDPlayer9\vms\video\my_gameplay.mp4 --every 1.0
```

圖片會輸出到 `recordings/frames/<影片名>/`。

> **「每新增一個影片就生成對應腳本」的運作方式**：watcher 負責偵測新影片並抽幀，
> 但腳本內容需要 AI 看圖判讀按鈕與流程才能產生，無法純機器完成。
> 流程為：`watch` 抽幀 → 在對話跟 Claude 說「分析 <影片名>」→ Claude 產出 `scripts/<影片名>.yaml`。

### 3. 由 Claude 分析產生測試腳本

把抽出的影格交給 Claude（本對話即可），說明你要測的流程。Claude 會：
- 看圖判讀每個畫面與按鈕位置
- 產出 `scripts/xxx.yaml`（正規化座標 + 圖像斷言）
- 列出需要的模板圖清單

### 4. 製作模板圖

對需要做圖像比對的畫面，用內建截圖工具抓圖後裁切出按鈕/標誌，存到 `assets/`：

```powershell
py run.py capture assets\raw_main.png
# 再用任何看圖軟體裁切出 btn_start.png / main_screen_hud.png ... 放進 assets\
```

### 5. 執行測試

```powershell
py run.py test scripts\example_login.yaml --repeat 10
```

執行完成後自動開啟 HTML 報告，路徑在 `results/<腳本名>_<時間>/report.html`。

## 腳本格式

座標全部是 0~1 比例。支援動作：

| action | 說明 | 參數 |
|---|---|---|
| `tap` | 點擊比例座標 | `x`, `y` |
| `tap_image` | 找到圖片再點 | `template`, `timeout`, `region` |
| `swipe` | 滑動 | `x1,y1,x2,y2`, `duration_ms` |
| `wait` | 等待秒數 | `seconds` |
| `wait_image` | 等圖片出現 | `template`, `timeout`, `region` |
| `assert_image` | 斷言圖片存在（判成敗） | `template`, `region` |
| `assert_absent` | 斷言圖片不存在 | `template`, `region` |
| `input_text` | 輸入文字 | `text` |
| `key` | 按鍵 | `keycode`(`back`/`home`/數字) |
| `screenshot` | 主動截圖 | （用 `name` 命名） |

- `critical: true` 的步驟失敗會讓整輪判為 FAIL；`assert_*` 預設即為 critical。
- `region: [x1,y1,x2,y2]`（比例）可縮小圖像搜尋範圍，加速且更準。

範例見 [scripts/example_login.yaml](scripts/example_login.yaml)。

## 專案結構

```
GameTestAi/
├── config/settings.yaml      # 路徑、解析度、重複次數、比對門檻
├── gametest/
│   ├── config.py             # 設定載入
│   ├── ldplayer.py           # ldconsole 包裝（解析度/啟停）
│   ├── adb.py                # adb 包裝（輸入/截圖/連線）
│   ├── device.py             # 高階裝置（正規化座標）
│   ├── matcher.py            # OpenCV 多尺度模板比對
│   ├── script_model.py       # 腳本 YAML 資料模型
│   ├── actions.py            # 步驟執行器
│   ├── runner.py             # 解析度 × N 次編排
│   ├── report.py             # JSON / HTML 報告
│   └── video.py              # 影片抽幀
├── scripts/                  # 測試腳本 (*.yaml)
├── assets/                   # 模板圖
├── recordings/               # 上傳的錄影與抽出的影格
├── results/                  # 測試產出（截圖 + 報告）
└── run.py                    # CLI 入口
```

## 設計重點

- **解析度切換**：`ldconsole modify --resolution W,H,DPI` 需在實例關閉時設定，下次啟動才生效。`Device.prepare()` 會自動「關閉 → 設定 → 啟動 → 等開機完成」。
- **截圖正確性**：用 `adb exec-out screencap -p` 取原始位元組，避免 Windows CRLF 破壞 PNG。
- **跨解析度比對**：`matcher.py` 以 `scale_min~scale_max` 多尺度縮放模板，吸收解析度差異。若比對不穩，調高 `assets` 圖品質或降低 `matching.threshold`。
```
