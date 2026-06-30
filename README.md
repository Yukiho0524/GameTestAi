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

## 圖形控制台（GUI）

```powershell
py run.py gui
```

一個視窗即可完成：勾選測試解析度（**橫版 / 直版分區**）、輸入包體名並按
「驗證 App 可開啟」確認 ADB 能啟動、「啟動雷電」套用解析度、選腳本後「執行測試」。
不要的腳本可在此選取後按「**刪除腳本**」——會一併清除影片對應並自動 commit + push 到 git。
偏好純命令列也可用下方各指令。

## 完整流程

### 1. 確認環境

```powershell
py run.py devices                 # 列出雷電實例與 adb 裝置
py run.py presets                 # 看所有解析度預設 key
py run.py apps --filter 關鍵字     # 找受測 App 的包體名
py run.py verify-app <包體名>      # 確認可用 ADB 開啟
```

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

執行完成後自動開啟 **Excel 報告**，路徑在 `results/<腳本名>_<時間>/report.xlsx`
（另有 `report.html` / `report.json` 備查）。

## 適配檢查報告（Excel）

報告主訴求是抓「各解析度下、模擬器跑是否會**圖歪 / 掉圖**」這類適配問題：

- **一個解析度一個頁籤** + 一個「總覽」頁，記錄測試次數、通過率、BUG 步驟數、崩潰次數
- **每次點擊前後都截圖**，與原影片對應畫面做正規化 SSIM 比對（跨解析度可比）
- 判定為 **BUG** 並在「BUG 明細」內嵌 `原影片 / 點擊前 / 點擊後` 縮圖的情況：
  - 點擊前畫面與原影片相似度過低（圖歪/版面跑掉）
  - 點擊後畫面無變化（按鈕無反應）或與預期結果不符（流程跑掉）
  - 黑屏 / 純色 / 掉圖、洋紅占位（材質遺失）
  - 實際解析度與預期不符、letterbox 黑邊比例異常
  - logcat 偵測到 App 崩潰 / ANR

比對用的預期畫面來自腳本步驟的 `reference`（點擊前）與 `reference_after`（點擊後），
由 AI 生成腳本時從影格自動存出。

## 修正腳本的迭代流程

影片無法 100% 判斷操作意圖（最常見：短點 vs 長壓），腳本可能跑不出預期。流程：

```powershell
py run.py test scripts\20260630_01.yaml --once   # 快速跑一次（單解析度單次）
py run.py diagnose                               # 列出失敗/可疑步驟 + 修正建議
```

`diagnose` 會指出每個出問題的步驟、現象與建議，例如：
- 「此步短點無反應、自動長壓才成功 → 建議改 `long_press` 或 `press: long`」
- 「模板比對失敗 → 重截 template / 加 region / 調 threshold」
- 「畫面與原影片差異大 → 可能是適配 BUG 或 reference 不準」

依建議改腳本 → 再 `--once` 驗證 → 沒問題後跑完整 `test`。`press: auto` 能讓第一次跑就自動試出短點/長壓，減少手動來回。

## 錄影 → 自動命名 → 自動推 git

`watch` 偵測新影片並抽幀後，請 Claude 分析產生腳本。腳本會：
- **自動命名**為 `當天日期_當天第幾隻`（例 `20260630_01.yaml`、`20260630_02.yaml`）
- 影片↔腳本對應記在 `scripts/.video_index.json`，避免重複生成
- 自動 `git commit`（`[Hibari] ...`）並 `push origin main`

## 腳本格式

座標全部是 0~1 比例。支援動作：

| action | 說明 | 參數 |
|---|---|---|
| `tap` | 點擊比例座標 | `x`, `y` |
| `tap_image` | 找到圖片再點 | `template`, `timeout`, `region` |
| `long_press` | 長壓比例座標 | `x`, `y`, `duration_ms`(預設800) |
| `long_press_image` | 找到圖片再長壓 | `template`, `duration_ms`, `timeout`, `region` |
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
- **短點 vs 長壓分不清時用 `press`**：`tap` / `tap_image` 可加 `press: tap|long|auto`。
  影片看不出是短點還長壓時就標 `press: auto` —— runtime 先短點，偵測到「點擊後無反應」會**自動改長壓重試並記錄實際生效的方式**，第一次跑完就知道答案。
- **長壓**：`long_press` / `long_press_image` 用 `duration_ms` 控制按住時間（毫秒）。
- **滑動**：`swipe` 的 `duration_ms` 越大滑越慢；甩動/翻頁用小值、拖曳用大值。
- **文字輸入**：`input_text` 走 ADB `input text`，**只支援英數**。要輸入**中文**需在模擬器裝 [ADBKeyboard](https://github.com/senzhk/ADBKeyBoard) 並設為輸入法，跟我說一聲我再把 input_text 切成走 ADBKeyboard 廣播。

範例見 [scripts/example_login.yaml](scripts/example_login.yaml)。

### 起始狀態處理（錄影沒從 App 啟動點開始時）

腳本可在最上層加兩個選用欄位，吸收「錄影起點與冷啟動不一致」的問題：

```yaml
prelude: prelude_baseline.yaml   # 方案 B：先跑這支把 App 從冷啟動帶到 baseline
anchor:                          # 方案 A：冷啟動後先等此畫面出現才開始主步驟
  template: main_screen_hud.png
  timeout: 40
```

- **anchor**：runner 冷啟動 App 後，會先 `wait_image(anchor)` 同步到錄影的起始畫面才開始跑 `steps`。逾時會明確標記「起始狀態錯位」，提示從啟動點重錄。
- **prelude**：可重用的「launch→baseline」導航腳本（見 [scripts/prelude_baseline.yaml](scripts/prelude_baseline.yaml)），多支 feature 腳本可共用同一份登入/過場流程。
- 兩者可並用：先 prelude 導航到 baseline，再用 anchor 確認，最後跑主步驟。
- 解析度可用 `test.resolution_presets: [ld_1280x720, pt_1080x1920]` 以預設 key 指定（`py run.py presets` 查所有 key）。

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
