# GameTestAi — 專案說明與腳本生成規範

雷電模擬器手遊自動化測試系統。流程：操作員錄遊戲影片 → 抽幀 → **AI 看影格生成測試腳本** → 跨解析度重複執行 → Excel 適配報告。

## 從影片影格自動生成測試腳本（給自動化/headless 任務看）

當被要求「分析某支影片並生成腳本」時，依下列規範執行：

1. **影格位置**：`recordings/frames/<影片名不含副檔名>/`。挑代表性影格判讀流程（黑屏/載入/標題/各畫面/操作結果）。用 Read 工具看圖。

2. **座標一律正規化 0~1**（左上 0,0、右下 1,1）。但跨解析度真正靠的是 `tap_image`
   圖像比對（座標只用於生成時「指出要裁哪個圖案」），runtime 找圖案而非去固定座標。
   - 模板要**緊貼元件本體裁**（勿以點擊座標為中心切含背景的大方塊；背景隨解析度變會拉低比對）。
   - 錄影建議用「要測的最高解析度」，模板縮小比對品質優於放大。
   - 同方向才一起測（橫版錄勿測直版，版面會整個重排）。
   - 能用 `tap_image` 就用；`tap_scene`（座標式）只留給點空白處/無可辨識圖案，跨解析度易點偏。

3. **動作**（見 `gametest/script_model.py` 的 ACTIONS）：`tap / tap_image / long_press / long_press_image / swipe / wait / wait_image / assert_image / assert_absent / input_text / key / screenshot`。

   **scene-gate（每步先確認畫面）**：任何步驟可加 `scene: {template: refs/...(整張畫面), timeout, [threshold], [mode: bands|full], [region]}`。runtime 會在動作前先等目前畫面與該參考「夠像」（預設比穩定 UI 帶 bands、門檻 cfg.scene_threshold≈0.7）才執行；等不到就明確報「畫面不符」（區分於「按鈕沒找到」）。genscript 會自動為每個點擊步驟附上錄影當下整張畫面當 scene。tap_image 也可加步驟內 `threshold` 覆寫比對門檻。

   **until（點擊後置條件，根治點擊被吞）**：點擊步驟可加 `until: {template: refs/...(下一畫面), timeout}`。點擊後 runtime 等該畫面出現；等不到且**原按鈕還在原地**→自動補點（press:auto 短/長交替，最多 4 次）；已離開原畫面但目標未確認→軟通過交給下一步 scene-gate 仲裁。genscript 由影片**畫面分段**（`gametest/videoseg.py`）自動判斷哪些點擊觸發轉場並附上 until。

   **fallback_x/fallback_y（座標後備）**：tap_image 可帶錄影實測正規化座標；scene-gate 已確認在對的畫面、模板卻比不到（帳號狀態使按鈕外觀不同）時→改點該座標，不放棄。genscript 自動附上。

   **時間軸注意**：taps.json 時間比影片快（screenrecord 啟動延遲），且每個點擊的按壓反饋 lag 差異大（實測 0.3~1.2s）。genscript 用 `press_onsets()` 逐點量測、在「按壓前一刻」裁常態外觀模板——手動裁圖時務必確認該幀在按壓之前。

4. **不確定短點還長壓** → 用 `press: auto`（runtime 會先短點、無反應自動改長壓並記錄）。

5. **anchor**（腳本頂層）：冷啟動後先等的起始畫面模板，吸收載入時間/起始錯位。

6. **點擊策略（核心，務必遵守）——圖像式點擊，等同 Airtest，不點座標**：
   原則是「在當前畫面**找到被點的那個圖案** → 點它」，而不是記座標。
   - **一律用 `tap_image`**：判讀操作員點了哪個按鈕/圖示，用 cv2 從該影格把**那個圖案**裁成模板存 `assets/<影片名>/`，腳本用 `tap_image` 指向它。runtime 會在當前畫面用多尺度比對找到該圖案、點它的中心（跨解析度自動定位）。這就是 Airtest `touch(Template(...))` 的效果。
   - **每個 click 步驟都要對應一張裁好的圖案模板**；裁完務必用 Read 檢視，確認框到的就是該元件（不要框到背景/相鄰元件）。
   - 同一元件在不同狀態（選中/未選）外觀不同時，裁「未選/常態」那張當模板。
   - 真的沒有可辨識圖案（點空白處/拖曳/座標型操作）才退用 **`tap_scene`**：附 `reference`（錄影當下整張畫面）+ 正規化 `x,y`，runtime 先比對畫面相符才點，不符不亂點。
   - **禁止**產生沒有任何畫面驗證的純 `tap` 盲點座標。
   - 關鍵畫面用 `assert_image` 判成敗。可附 `reference`/`reference_after` 做適配比對。
   - 找不到操作員確切點哪時：最佳解是請操作員開啟雷電「顯示點按操作」重錄，標記會顯示點擊位置，即可精準裁出被點圖案（見「觸控標記」一節）。

7. **重用既有模板/參考圖**：同款遊戲已有的 `assets/` 模板優先重用（如本專案 `assets/video1/` 的 title_logo、btn_enter_game、main_hud）。需要新模板時，用 cv2 從影格裁切存到 `assets/<影片名>/`，參考影格存到 `assets/refs/<腳本名>/`。

8. **不確定的座標**：填影格估算值並在該步 `name` 標 `[需校正]` + 註解，供之後 `--once` + `diagnose` 在裝置上校正。無法判讀的流程段落用 YAML 註解標 TODO。

9. **落檔 + 命名 + 推 git**：用 `gametest.scriptgen.save_and_push(cfg, yaml_text, video_name='<原始檔名.mp4>')`。
   - 自動命名 `YYYYMMDD_NN`（當天日期＋當天第幾隻）
   - 自動登記 `scripts/.video_index.json` 影片↔腳本對應
   - 自動 `git commit`（訊息以 `[Hibari] ` 開頭）+ `push origin main`
   - 若有新增 `assets/` 模板/參考圖，記得一併 `git add` 後再 push（save_and_push 只加腳本與索引）。

10. 參考既有範例：`scripts/20260630_01.yaml`、`scripts/20260630_02.yaml`、`scripts/example_login.yaml`、`scripts/prelude_baseline.yaml`。

## 精確點擊位置（getevent，優先使用）

雷電不會把「顯示點按操作」標記畫進畫面，**但真實點擊會經過 `/dev/input/event2`**。
透過 GUI「開始/停止錄影」錄製時，會同步用 getevent 擷取觸控，產生 **taps.json**：
每筆含 `t`(影片相對秒)、`nx,ny`(正規化座標)、`duration_ms`、`kind`(tap/long_press/swipe)。
- 單段錄影：`<影片>.mp4.taps.json`
- 超過 3 分鐘會自動分段成 session 資料夾 `rec_<ts>/`（含 `part01.mp4...`、`session.json`、
  `taps.json`）；抽幀會把各段依序連續處理，taps.json 時間軸也連續 → 當**一個流程**生成一支腳本。
  用 `gametest.video.taps_json_for(來源)` 取得對應 taps.json。

**生成腳本時，若來源影片有對應的 `.taps.json`：優先用它的精確座標**——
對每筆觸控，從「`t` 當下（或前一幀）」的影格、以 `(nx,ny)` 為中心裁出**被點的圖案**當模板，
產生 `tap_image`（kind=long_press → `long_press_image`，swipe → `swipe` 用 nx,ny→end_nx,end_ny）。
這樣點擊位置是輸入層實測、不是估算。沒有 taps.json 時才退回看畫面判讀。

（`gametest/touchdetect.py` 的影像偵測在雷電上不可靠，已被 getevent 取代。）

## AI 指令模式（自然語言命令 → AI 自主操作遊戲）

GUI「🤖 AI 指令」輸入框或 `py -m gametest.aimission "<命令>"` 會呼叫 Claude headless，
以「截圖→判讀→操作」循環自主完成命令（如「進入遊戲，開商城買一次雞精」），
截圖記錄存 `results/ai_mission_<ts>/`。被指派此類任務時：

1. **工具**：`py -m gametest.aidrive`（boot / shot / tap / long / swipe / back / text，
   座標一律正規化 0~1）。`boot` 開雷電+啟動遊戲並印任務資料夾；`shot` 截圖後**用 Read 看圖**再決定動作。
2. **進入遊戲**：點「進入遊戲」可能被載入吞掉——點完 shot 確認離開標題，沒離開就再點
   （短/長交替）；進入後載入可能 30~60s，用已知模板（assets/ 下 city_hud 等）或看圖確認到達。
3. **非預期彈窗**（問卷/公告/更新/劇情對話）：找 X / SKIP / 確認 關掉再繼續，這是常態不是錯誤。
4. **結尾**：達成→`shot final_state` + 印「MISSION DONE: <結果>」；無法達成→「MISSION FAILED: <原因>」。
   金流類操作（購買）以「成功訊息 + 貨幣扣款數字」雙重確認。
5. adb 偶發 device offline：先 `adb connect 127.0.0.1:5555` 重試，模擬器程序不在才算環境中斷。

## 慣例

- 每完成一版都 `git push origin main`；commit 訊息以 `[Hibari] ` 開頭。
- 環境：Windows，Python 用 `py` 啟動器（PATH 上沒有 `python`，腳本勿加 shebang）。
