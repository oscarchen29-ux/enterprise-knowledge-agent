# 校系行政助理 Agent(大三專題)

本地部署的多步驟校系行政問答 Agent,以國立暨南國際大學資訊工程學系為場景。
用 Ollama 跑本地開源模型,不需要把學生的提問送到外部雲端。
Provider 抽象層讓之後換成 Claude API 或 NVIDIA DGX Spark 的推論服務時,agent 邏輯不用重寫。

---

> **接手部署或修改之前,請先讀 [TROUBLESHOOTING.md](TROUBLESHOOTING.md)。**
> 這個專案踩過的坑大多是「不會報錯但結果是錯的」那一類 —— 尤其是
> **換模型**:換一個更大、榜單更漂亮的模型很可能反而不能跑,而且失敗是無聲的。

## 快速開始

```bash
pip install -r requirements.txt
ollama serve
ollama pull qwen2.5:7b
python agent.py --task "資工系大三的必修有哪些?"
```

換模型之前**先驗相容性**(不是每個模型都能用,見下方「換模型的硬性條件」):

```bash
python scripts/check_model.py qwen2.5:14b
```

---

## 知識庫

`docs/` 有 110 份文字檔,全部由 `docs_source/` 的 89 份官方 PDF 自動轉出。

**每一份都可追溯來源。** `docs_source/MANIFEST.tsv` 記錄每個 PDF 的原始網址、下載時間、
SHA-256 與檔案大小;每個 `.txt` 開頭也帶著同樣的出處資訊。這是刻意的要求 ——
早期版本是手工摘要,出處只有檔案裡自己打的一行字,結果其中兩份被發現內容有誤
(學士班科目表停在 110 學年度,而且轉檔時整欄「開課年級」被丟掉),而且無從查證。

來源分布:

| 來源 | 份數 | 內容 |
|---|---|---|
| `www.csie.ncnu.edu.tw` | 24 | 學士/碩士/博士班科目表(依入學屆別各一份)、輔系、雙主修、課程地圖、修業規則、系上表單 |
| `assistance.ncnu.edu.tw` | 17 | 請假規則、獎懲、操行、申訴、各項獎助學金 |
| `regist.ncnu.edu.tw` | 16 | 學則、學位考試辦法、轉系、離校程序、請領證件 |
| `housing.ncnu.edu.tw` | 13 | 宿舍床位申請、住宿費收退、寒暑假住宿、宿舍公約 |
| `health.ncnu.edu.tw` | 7 | 健康檢查、緊急傷病處理、校安 |
| `ltrc.ncnu.edu.tw` | 4 | 畢業外語能力門檻、英檢與第二外語獎勵 |
| `admission.ncnu.edu.tw` | 4 | 碩士班優秀學生獎勵、學士班新生入學獎勵 |
| `curriculum.ncnu.edu.tw` | 3 | 學雜費收費標準、抵免作業日程與流程 |
| `b027.ncnu.edu.tw` | 1 | 學務處學生手冊(236 頁,依分篇切成 23 個檔案) |

### 屆別是一等公民

科目表、碩士班優秀學生獎勵辦法、學士班新生入學獎勵辦法都是**依入學學年度分版本發布**的,
規定確實不同 —— 例如學士班共同課程與通識學分,111、112 級是 15/16,113 級起改成 16/15。
因此知識庫保留各屆版本而非只留最新版,檔名與內文都標註適用屆別。

### 尚未尋獲

`學位論文格式與繳交注意事項`、`職涯暨實習優秀學生獎學金實施要點`、
`資工系產學共構學分學程規畫書` —— 在校內各處室網站上都沒找到公開全文。
未經查證的版本不放進知識庫。

---

## 架構

```
providers/
  base.py                # LLMProvider 抽象介面(OpenAI 風格的訊息格式)
  ollama_provider.py     # 本地 Ollama 實作,走原生 /api/chat
tools.py                 # 工具:BM25 切塊檢索
agent.py                 # planner-executor 主迴圈 + 自我驗證 + 繁簡後處理
docs/                    # 知識庫文字檔(由腳本產生,不要手改)
docs_source/             # 官方 PDF 原始檔 + MANIFEST.tsv 出處紀錄
scripts/
  build_docs.py          # docs_source/*.pdf -> docs/*.txt,可重跑
  fetch.py               # 下載並登記出處
  sources_*.py           # 各批次的來源清單
  check_model.py         # 換模型前的相容性檢查
benchmark/               # 評估題組與執行器(題目尚待依新知識庫重寫)
```

`docs/` 是產生物。要更新知識庫,是把新 PDF 放進 `docs_source/`(用 `scripts/fetch.py`
登記出處)然後重跑 `python scripts/build_docs.py`,不是直接編輯 `.txt`。

---

## 換模型的硬性條件

**不是任何 Ollama 模型都能直接換上去,而且「更強」不等於「更適合」。**
完整說明見 [TROUBLESHOOTING.md 第一節](TROUBLESHOOTING.md#一換模型更強不等於更適合)。

實測過的反例:`qwen2.5:14b`(9.0GB)比這台機器的顯卡(8.0GB)還大,會 offload 到
CPU;而且在本專案的測試裡,**14B 因工具呼叫格式錯誤而失敗,7B 反而答對**。

四個條件,缺一不可:

1. **必須支援 tool calling。** agent 每一步都靠它查文件。模型若不支援,Ollama 會
   直接忽略 `tools` 參數,agent 完全不會檢索,答案沒有任何文件依據 —— 而且不會報錯。
   用 `scripts/check_model.py` 看 `capabilities` 有沒有 `tools`。
2. **context 上限要 ≥ 16384。** 一次檢索回傳約 4~9k tokens。
3. **要塞得進 VRAM。** 開發機是 RTX 5070 Laptop 8GB;9GB 的 qwen2.5:14b 塞不進去,
   會 offload 到 CPU,慢很多。
4. **中文品質要夠。** 簡體字有 OpenCC 後處理接住,但理解與用詞品質沒有工具能補。

---

## 已知的坑(摘要,完整版見 [TROUBLESHOOTING.md](TROUBLESHOOTING.md))

- **Ollama 的 `num_ctx` 預設 2048**,不管模型宣稱支援多長。檢索回傳的文件會被無聲截掉
  七成以上。而且這個設定**無法透過 OpenAI 相容層傳入**(`extra_body` 無效),只有原生
  `/api/chat` 會生效 —— 這是 `ollama_provider.py` 不用 openai 套件的原因。
- **模型會把工具呼叫寫成純文字**(常伴隨無意義 token,例如「 Closet」),Ollama 不解析,
  agent 誤判為「不需要查文件」。`agent.py` 會把這種外洩的呼叫解析回來執行。
- **完全不查就作答**:另有一道保底檢索,模型沒查任何文件時強制查一次再重答。
- **Windows / Git Bash 會弄壞中文檔名**(觀察到「教育」被寫成「教灶」),所以下載與
  轉檔都走 Python,不用 shell heredoc 傳中文。主控台 cp950 也印不出部分字元,
  腳本一律把 stdout 轉成 UTF-8。

---

## 現況

- [x] Provider 抽象介面 + Ollama 實作(原生 API,可控 `num_ctx`)
- [x] planner-executor 迴圈 + tool calling,含外洩呼叫救回與保底檢索
- [x] 自我驗證步驟
- [x] 知識庫改由官方 PDF 自動轉出,全部可追溯出處
- [x] BM25 切塊檢索(取代原本回傳整份文件的關鍵字比對)
- [ ] 依新知識庫重寫 benchmark 題目
- [ ] 向量檢索(embedding)
- [ ] 接 Claude API 做雲端對照組
- [ ] 接 DGX Spark 推論服務
- [ ] 質化訪談

---

## 給部署者的提醒

這個系統**會答錯**。實測同一個問題三次,可能一次正確、一次誤答「文件未提及」、
一次把相鄰條文的內容混進來。上線給學生使用時建議:

- **顯示出處**(`search_documents` 回傳的片段已標註檔名與段號),讓使用者能自行核對
- 明確標示這不是官方系統,以系辦公告為準
- 提供回報管道 —— 真實提問與錯誤回報,正是這個專題最需要的評估資料
- 8GB 顯卡一次只能處理一個請求,並發時需要排隊
