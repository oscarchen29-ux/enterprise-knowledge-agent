"""用 bge-m3 官方套件(FlagEmbedding)把 docs/ 的每一塊算成 dense 向量與 sparse 詞權重。

為什麼另外建一份索引:Ollama 的 /api/embed 只回傳 bge-m3 的 dense 向量,拿不到
模型本身就會算的 sparse(詞權重)輸出。bge-m3 論文(Chen et al., ACL Findings 2024)
的 sparse 在結構上就是「學出來的 BM25」:只對問題和段落共同出現的 token 計分,但
每個 token 的權重由模型依上下文給,不是 TF-IDF 公式。

在本專案的消融實驗(benchmark 內 gemma4:31b 寫出的查詢,段落層級 n=96)上:
    我們的 BM25                      70.8%
    bge-m3 sparse                    76.0%
    bge-m3 0.2·dense + 0.8·sparse    76.0%;換成 qwen2.5 等其他模型寫的查詢重測仍最穩定
詳見 benchmark/RESULTS.md。

需要 PyTorch 與 FlagEmbedding(見 requirements.txt 的選用區塊)。沒有這兩個套件的
機器不必執行本程式,tools.py 會自動退回 BM25 + 向量的 RRF 混合檢索。

索引是產生物,改動 docs/ 之後要重跑:

    python scripts/build_m3_index.py

CPU 上 821 塊約 6 分鐘;第一次執行會從 Hugging Face 下載 BAAI/bge-m3(約 2.2 GB)。
"""

import io
import json
import os
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402

from tools import M3_MODEL, _load_chunks, chunk_fingerprint, m3_encoder  # noqa: E402

INDEX_DIR = os.path.join(ROOT, "index")


def main():
    chunks = _load_chunks()
    print(f"共 {len(chunks)} 塊,使用 {M3_MODEL}(FlagEmbedding)建立 dense + sparse 索引")
    started = time.time()
    model = m3_encoder()
    # max_length 1024:切塊上限 600 字,中文約一字一 token,1024 足以不截斷
    output = model.encode([c["text"] for c in chunks], batch_size=8, max_length=1024,
                          return_dense=True, return_sparse=True, return_colbert_vecs=False)

    dense = np.asarray(output["dense_vecs"], dtype=np.float32)
    norms = np.linalg.norm(dense, axis=1, keepdims=True)
    dense /= np.where(norms == 0, 1, norms)
    # sparse 權重以 token id(字串)為鍵,只存非零項 —— 821 塊合計只有數萬個條目
    sparse = [{token: round(float(weight), 5) for token, weight in weights.items()}
              for weights in output["lexical_weights"]]

    os.makedirs(INDEX_DIR, exist_ok=True)
    np.save(os.path.join(INDEX_DIR, "m3_dense.npy"), dense)
    with io.open(os.path.join(INDEX_DIR, "m3_sparse.json"), "w", encoding="utf-8") as f:
        json.dump(sparse, f, separators=(",", ":"))
    meta = {
        "model": M3_MODEL,
        "dimensions": int(dense.shape[1]),
        "chunk_count": len(chunks),
        "fingerprint": chunk_fingerprint(),
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    with io.open(os.path.join(INDEX_DIR, "m3_meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    terms = sum(len(w) for w in sparse)
    print(f"完成:dense {dense.shape[0]} x {dense.shape[1]},sparse 共 {terms} 個非零權重,"
          f"耗時 {time.time() - started:.0f}s")


if __name__ == "__main__":
    main()
