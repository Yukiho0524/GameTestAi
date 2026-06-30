# GameTestAi — 專案說明與腳本生成規範

雷電模擬器手遊自動化測試系統。流程：操作員錄遊戲影片 → 抽幀 → **AI 看影格生成測試腳本** → 跨解析度重複執行 → Excel 適配報告。

## 從影片影格自動生成測試腳本（給自動化/headless 任務看）

當被要求「分析某支影片並生成腳本」時，依下列規範執行：

1. **影格位置**：`recordings/frames/<影片名不含副檔名>/`。挑代表性影格判讀流程（黑屏/載入/標題/各畫面/操作結果）。用 Read 工具看圖。

2. **座標一律正規化 0~1**（左上 0,0、右下 1,1），腳本才能跨解析度跑。

3. **動作**（見 `gametest/script_model.py` 的 ACTIONS）：`tap / tap_image / long_press / long_press_image / swipe / wait / wait_image / assert_image / assert_absent / input_text / key / screenshot`。

4. **不確定短點還長壓** → 用 `press: auto`（runtime 會先短點、無反應自動改長壓並記錄）。

5. **anchor**（腳本頂層）：冷啟動後先等的起始畫面模板，吸收載入時間/起始錯位。

6. **圖像比對**：互動步驟盡量用 `tap_image`（模板放 `assets/`，跨解析度靠多尺度比對）。關鍵畫面用 `assert_image` 判成敗。可附 `reference`（點擊前預期畫面）/`reference_after`（點擊後預期畫面）做適配比對。

7. **重用既有模板/參考圖**：同款遊戲已有的 `assets/` 模板優先重用（如本專案 `assets/video1/` 的 title_logo、btn_enter_game、main_hud）。需要新模板時，用 cv2 從影格裁切存到 `assets/<影片名>/`，參考影格存到 `assets/refs/<腳本名>/`。

8. **不確定的座標**：填影格估算值並在該步 `name` 標 `[需校正]` + 註解，供之後 `--once` + `diagnose` 在裝置上校正。無法判讀的流程段落用 YAML 註解標 TODO。

9. **落檔 + 命名 + 推 git**：用 `gametest.scriptgen.save_and_push(cfg, yaml_text, video_name='<原始檔名.mp4>')`。
   - 自動命名 `YYYYMMDD_NN`（當天日期＋當天第幾隻）
   - 自動登記 `scripts/.video_index.json` 影片↔腳本對應
   - 自動 `git commit`（訊息以 `[Hibari] ` 開頭）+ `push origin main`
   - 若有新增 `assets/` 模板/參考圖，記得一併 `git add` 後再 push（save_and_push 只加腳本與索引）。

10. 參考既有範例：`scripts/20260630_01.yaml`（除錯登入流程）、`scripts/20260630_02.yaml`（進入遊戲+商品管理）、`scripts/example_login.yaml`、`scripts/prelude_baseline.yaml`。

## 受測遊戲（目前）
悍利商店 POCKET STORE（Gamania）。包體名 `com.gamania.pocketstorem.gama`。
除錯登入：**左上驚嘆號長壓**開 SRDebugger → OPTIONS 分頁 → 輸入 **AccountID=100** → Login。

## 慣例
- 每完成一版都 `git push origin main`；commit 訊息以 `[Hibari] ` 開頭。
- 環境：Windows，Python 用 `py` 啟動器（PATH 上沒有 `python`，腳本勿加 shebang）。
