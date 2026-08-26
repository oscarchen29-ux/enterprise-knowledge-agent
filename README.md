# 校系行政助理 Agent(大三專題)

本地部署的多步驟校系行政任務 Agent,以國立暨南國際大學資訊工程學系為場景。目前用 Ollama 跑本地模型做邏輯原型驗證,之後可以直接換成 Claude API 或 NVIDIA DGX Spark 上的推論服務,不需要重寫 agent 邏輯。除了技術評估,後續會加入質化訪談(學生/系辦人員)評估實際使用效益。

## 目前知識庫文件(docs/,17份,取自暨大公開網站)
校級規定:學則、請假規則、離校程序實施要點、學生獎懲辦法、請領學籍暨成績證件規則、學雜費收費標準一覽表、學生抵免學分辦法
資工系課程:學士班/碩士班/博士班必選修科目表、碩士班修業規則、產學共構學分學程規畫書
學位相關:研究生學位考試辦法、學位論文格式與繳交注意事項
獎學金:外語畢業門檻與獎勵辦法、碩士班優秀學生獎勵辦法、職涯暨實習優秀學生獎學金實施要點

未找到公開全文、故未收錄:系版選課須知(僅有會過期的學期公告)、完整清寒獎助學金辦法、大學部獨立企業實習辦法、資工系博士資格考細則。正式評估前建議跟系辦確認是否有更新版本或內部文件可補齊這些缺口。

**已知技術限制**:目前 `search_documents` 是關鍵字比對後回傳「整份文件全文」,文件數變多、`學則.txt` 這類單檔案較大(21KB)後,一次查詢可能命中多份文件、塞爆 context,造成回答品質下降。下一步該優先做**向量檢索(embedding + chunk)**,只回傳最相關的段落而非整份文件。

## 架構

```
providers/
  base.py              # LLMProvider 抽象介面
  ollama_provider.py   # 本地 Ollama 實作(現在用這個)
  # claude_provider.py # 之後申請 API key 再加
  # spark_provider.py  # 之後拿到 Spark 權限再加

tools.py               # agent 可呼叫的工具(目前:文件搜尋)
agent.py               # planner-executor 主迴圈
docs/                  # 暨大公開行政規定文件,給文件搜尋工具用
```

## 環境設置

```bash
pip install -r requirements.txt
ollama serve          # 確保 Ollama 服務有在跑
ollama pull qwen2.5:7b
```

## 執行

```bash
python agent.py
```

## 現況

- [x] Provider 抽象介面 + Ollama 實作
- [x] 最小可跑的 planner-executor 迴圈(關鍵字搜尋工具)
- [ ] 換成向量檢索(embedding + Chroma/Milvus)
- [ ] 加入自我驗證/反思步驟
- [ ] 接 Claude API 做雲端對照組
- [ ] 建立多步驟任務 benchmark
- [ ] 接 DGX Spark 推論服務
