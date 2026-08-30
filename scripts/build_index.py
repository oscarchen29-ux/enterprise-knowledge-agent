"""把 docs/ 的每一塊算成向量,存成索引供混合檢索使用。

為什麼需要向量:BM25 比對的是字面。使用者問「診斷證明」,規則裡寫的是
「校醫或醫院證明」—— 字不一樣就完全比不到,這是關鍵字檢索的結構性死角。
實測 bge-m3 對這兩者的餘弦相似度是 0.787,而對不相關的「宿舍冷氣費」只有 0.451。

索引是產生物,改動 docs/ 之後要重跑:

    python scripts/build_index.py

模型 bge-m3 是多語模型、中文表現好、支援長輸入,1024 維。
換模型的話 index/ 必須整個重建 —— 不同模型的向量空間不能混用。
"""

import hashlib
import io
import json
import os
import sys
import time
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402

from tools import _load_chunks, chunk_fingerprint  # noqa: E402

INDEX_DIR = os.path.join(ROOT, "index")
EMBED_MODEL = "bge-m3"
BATCH = 16          # 太大在 8GB 顯卡上容易失敗,16 是實測穩定的值
OLLAMA = "http://localhost:11434"


def embed(texts: list[str]) -> list[list[float]]:
    request = urllib.request.Request(
        f"{OLLAMA}/api/embed",
        data=json.dumps({"model": EMBED_MODEL, "input": texts},
                        ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=600) as response:
        return json.load(response)["embeddings"]


def main():
    chunks = _load_chunks()
    print(f"共 {len(chunks)} 塊,使用 {EMBED_MODEL} 建立索引")

    vectors = []
    started = time.time()
    for start in range(0, len(chunks), BATCH):
        batch = chunks[start:start + BATCH]
        vectors.extend(embed([c["text"] for c in batch]))
        done = min(start + BATCH, len(chunks))
        elapsed = time.time() - started
        rate = done / elapsed if elapsed else 0
        remaining = (len(chunks) - done) / rate if rate else 0
        print(f"\r  {done}/{len(chunks)}  已用 {elapsed:.0f}s  剩約 {remaining:.0f}s",
              end="", flush=True)
    print()

    matrix = np.asarray(vectors, dtype=np.float32)
    # 先做 L2 正規化,查詢時的餘弦相似度就只是一次內積,省掉每次重算範數
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    matrix /= np.where(norms == 0, 1, norms)

    os.makedirs(INDEX_DIR, exist_ok=True)
    np.save(os.path.join(INDEX_DIR, "embeddings.npy"), matrix)
    meta = {
        "model": EMBED_MODEL,
        "dimensions": int(matrix.shape[1]),
        "chunk_count": len(chunks),
        # 指紋用來偵測 docs/ 改過但索引沒重建的情況 —— 那會讓向量對到錯誤的塊,
        # 而且不會報錯,只是答案莫名其妙。
        "fingerprint": chunk_fingerprint(),
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    with io.open(os.path.join(INDEX_DIR, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    size = os.path.getsize(os.path.join(INDEX_DIR, "embeddings.npy")) / 1024 / 1024
    print(f"完成:{matrix.shape[0]} x {matrix.shape[1]},{size:.1f} MB,"
          f"耗時 {time.time() - started:.0f}s")
    print(f"索引位置:{INDEX_DIR}")


if __name__ == "__main__":
    main()
