# benchmark/runs/ — 每次執行的原始產物

這裡**只放資料,不放程式**。程式在上一層 `benchmark/`。

## 檔名怎麼讀

```
results_raw_<模型>_<設定>_<日期>_<時間>.jsonl     每題的完整紀錄,給程式分析用
scoring_sheet_<模型>_<設定>_<日期>_<時間>.csv      待人工評分的表格(Excel 可開,已加 BOM)
scoring_sheet_..._claude評分.csv                   已經評完分的版本
```

`<設定>` 常見的有:

| 標記 | 意思 |
|---|---|
| `think-off` / `think-on` | gemma4:31b 的思考模式開關 |
| `verify-verdict` | 自我查核改成「只給評語」而非直接改寫答案 |
| `merged` | 多次執行合併後的結果 |

## 哪幾批是重要的

| 檔案 | 為什麼重要 |
|---|---|
| `results_raw_qwen2.5-7b_20260830_233537.jsonl` | 修好文件轉檔之前的基準線 |
| `results_raw_gemma4-31b_think-off_20260911_160059_merged.jsonl` | 挑檢索權重時看過的那一組(選擇組) |
| `results_raw_gemma4-31b_think-on_20260911_211310.jsonl` | 沒看過的對照組,用來驗證權重不是過擬合 |
| `results_raw_qwen3.8-27b_2026092*.jsonl` | 9/23–24 的九次執行。**注意這批有 74 次因 SSH 斷線而失效**,詳見 RESULTS.md |

## 要重算指標不必重跑模型

```bash
python benchmark/recompute_metrics.py benchmark/runs/results_raw_qwen2.5-7b_20260830_233537.jsonl
python benchmark/inclusion_accuracy.py benchmark/runs/results_raw_A.jsonl benchmark/runs/results_raw_B.jsonl
```

## 為什麼這些檔案要進版控

因為專案的慣例是**所有量測都要可重現、可回查**。論文或報告裡引用的每一個數字,
都要能指回這裡的某一個檔案的某一題。刪掉就等於那個數字變成無法驗證的傳聞。

`run_log_*.txt` 是主控台輸出,在 Windows 下編碼為 cp950,**不進版控**(見 `.gitignore`),
資料一律以 `.jsonl` 為準。
