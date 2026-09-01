import hashlib
import json
import math
import os
import re
import urllib.request

DOCS_DIR = os.path.join(os.path.dirname(__file__), "docs")

# 切塊大小。太小會把一條法規切散、答案讀不完整;太大則退回原本「長文件塞爆 context」
# 的問題。600 字約可容納一到兩條完整條文,實測是可用的折衷。
CHUNK_SIZE = 600
CHUNK_OVERLAP = 120      # 讓被切在邊界上的句子在兩塊裡都出現,避免答案剛好被切斷
TOP_K = 6                # 回傳幾塊。塊比整份文件小很多,可以多給幾塊而不爆 context

# BM25 參數。k1 控制詞頻飽和速度,b 控制長度正規化強度。
# 取值依 Manning, Raghavan & Schütze, Introduction to Information Retrieval (2008)
# 第 11.4.3 節:k1 慣用 1.2–2.0,b 慣用 0.75(Lucene/Elasticsearch 的 b 預設也是
# 0.75,但 k1 預設是 1.2)。**未針對本專案資料做過參數搜尋**,這兩個值是直接沿用
# 慣例。要調的話成本很低:檢索是純演算法,不必跑模型,用 benchmark 的
# expected_docs 掃一遍參數空間即可。
BM25_K1 = 1.5
BM25_B = 0.75

# Reciprocal Rank Fusion 的平滑常數。60 出自 Cormack, Clarke & Büttcher, SIGIR 2009
# 提出 RRF 的那篇論文,後續實作多沿用。值越大越平均看待各名次。
RRF_K = 60

_CHUNK_CACHE = None


def _extract_keywords(query: str) -> list[str]:
    """模型常會丟出連續片語(例如「論文口試前考核」)而不是分開的關鍵字,
    純粹整串比對常常完全比不到,所以額外拆出中文2-gram當作候選關鍵字,
    提高比對到的機率(粗糙但堪用,之後應該換成真正的向量檢索)。"""
    tokens = query.replace(",", " ").replace("、", " ").replace(",", " ").split()
    bigrams = []
    for token in tokens:
        bigrams.extend(token[i:i + 2] for i in range(len(token) - 1))
    return tokens + bigrams


def _split(text: str) -> list[str]:
    """先照段落切,段落太長再照長度硬切,並保留重疊。

    照段落切是因為法規本來就以「第N條」分段,沿著段落邊界切比較不會把一條
    規定切成兩半。
    """
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks, buffer = [], ""
    for paragraph in paragraphs:
        # 【...】開頭的是章節標題,一律另起一塊,不與前一段合併。
        # 否則短小的章節會被黏回去:科目表的大一到大四各約 150 字,合併後又變成
        # 一塊涵蓋四個年級,查「大三」時再度被其他年級稀釋(實測排到第 14 名)。
        is_section = paragraph.startswith("【")
        if not is_section and len(buffer) + len(paragraph) <= CHUNK_SIZE:
            buffer = f"{buffer}\n{paragraph}" if buffer else paragraph
            continue
        if is_section and buffer:
            chunks.append(buffer)
            buffer = ""
        if is_section and len(paragraph) <= CHUNK_SIZE:
            buffer = paragraph
            continue
        if buffer:
            chunks.append(buffer)
        while len(paragraph) > CHUNK_SIZE:
            chunks.append(paragraph[:CHUNK_SIZE])
            paragraph = paragraph[CHUNK_SIZE - CHUNK_OVERLAP:]
        buffer = paragraph
    if buffer:
        chunks.append(buffer)
    return chunks


def _load_chunks() -> list[dict]:
    """把 docs/ 全部切成塊,只做一次。"""
    global _CHUNK_CACHE
    if _CHUNK_CACHE is not None:
        return _CHUNK_CACHE

    chunks = []
    for filename in sorted(os.listdir(DOCS_DIR)):
        if not filename.endswith(".txt"):
            continue
        with open(os.path.join(DOCS_DIR, filename), "r", encoding="utf-8") as f:
            content = f.read()
        # 出處區塊每個檔案都一樣,留在塊裡只會讓所有塊看起來都很像,反而干擾比對
        body = re.split(r"【條文】|【內容】|【畢業學分總覽】", content, maxsplit=1)
        body = body[-1] if len(body) > 1 else content
        for index, text in enumerate(_split(body)):
            chunks.append({"file": filename, "index": index, "text": text})

    _CHUNK_CACHE = chunks
    return chunks


def chunk_fingerprint() -> str:
    """目前這批塊的指紋,用來判斷向量索引是不是還對得上 docs/。

    索引過期不會報錯,只會讓向量對到錯的塊、答案莫名其妙,所以要能偵測。
    """
    digest = hashlib.sha256()
    for chunk in _load_chunks():
        digest.update(chunk["file"].encode("utf-8"))
        digest.update(str(len(chunk["text"])).encode("utf-8"))
    return digest.hexdigest()[:16]


_INDEX = None          # (向量矩陣, meta) 或 False 表示不可用
_INDEX_WARNED = False


def _load_index():
    """載入向量索引。缺檔或過期時退回純 BM25,不讓檢索整個壞掉。"""
    global _INDEX, _INDEX_WARNED
    if _INDEX is not None:
        return _INDEX

    directory = os.path.join(os.path.dirname(__file__), "index")
    vectors_path = os.path.join(directory, "embeddings.npy")
    meta_path = os.path.join(directory, "meta.json")

    def unavailable(reason):
        global _INDEX_WARNED
        if not _INDEX_WARNED:
            print(f"[檢索] 向量索引未啟用({reason}),改用純 BM25。"
                  f"執行 python scripts/build_index.py 可啟用混合檢索。")
            _INDEX_WARNED = True
        return False

    if not (os.path.exists(vectors_path) and os.path.exists(meta_path)):
        _INDEX = unavailable("索引不存在")
        return _INDEX
    try:
        import numpy as np
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        if meta.get("fingerprint") != chunk_fingerprint():
            _INDEX = unavailable("索引與目前的 docs/ 不一致,需要重建")
            return _INDEX
        _INDEX = (np.load(vectors_path), meta)
    except Exception as exc:  # noqa: BLE001
        _INDEX = unavailable(f"{type(exc).__name__}: {exc}")
    return _INDEX


def _vector_ranking(query: str) -> list[int] | None:
    """回傳依語意相似度排序的塊索引。索引不可用時回傳 None。"""
    index = _load_index()
    if not index:
        return None
    matrix, meta = index
    try:
        import numpy as np
        request = urllib.request.Request(
            "http://localhost:11434/api/embed",
            data=json.dumps({"model": meta["model"], "input": [query]},
                            ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            vector = np.asarray(json.load(response)["embeddings"][0], dtype=np.float32)
    except Exception:  # noqa: BLE001
        return None      # embedding 服務暫時不可用就退回 BM25,不要讓查詢失敗

    norm = np.linalg.norm(vector)
    if norm == 0:
        return None
    # 索引已在建立時做過 L2 正規化,餘弦相似度就是一次內積
    scores = matrix @ (vector / norm)
    return list(np.argsort(-scores))


_IDF_CACHE = {}


def _idf(keyword: str) -> float:
    """關鍵字的稀有度權重:出現在越少塊裡的詞,越能代表使用者真正想問什麼。

    值算過就快取,因為同一次查詢裡每一塊都會用到同一批關鍵字。
    """
    if keyword in _IDF_CACHE:
        return _IDF_CACHE[keyword]
    chunks = _load_chunks()
    frequency = sum(1 for c in chunks if keyword in c["text"])
    # +1 避免除以零;沒出現過的詞給 0,免得沒有訊號的雜訊詞反而拿到最高權重
    value = math.log(len(chunks) / (1 + frequency)) if frequency else 0.0
    _IDF_CACHE[keyword] = max(value, 0.0)
    return _IDF_CACHE[keyword]


def search_documents(query: str) -> str:
    """在 docs 資料夾裡用關鍵字比對,回傳最相關的文件片段。

    先前是回傳「整份文件」,在文件數擴充到 110 份之後出現兩個問題:一是命中密度
    會系統性偏袒短法規(1KB 的修業規則命中兩次,密度就贏過 20KB 科目表命中十次),
    查「大三必修」時科目表根本進不了前三名;二是三份全文動輒上萬字,大幅超出
    模型實際能吃的長度。改成切塊後,每塊長度相近,密度比較才有意義。
    """
    chunks = _load_chunks()
    keywords = set(_extract_keywords(query))
    average_length = sum(len(c["text"]) for c in chunks) / len(chunks)
    scored = []

    for position, chunk in enumerate(chunks):
        text = chunk["text"]
        score = 0.0
        for keyword in keywords:
            frequency = text.count(keyword)
            if not frequency:
                continue
            # BM25。之前只看關鍵字「有沒有出現」而不看出現幾次,又用 /sqrt(長度)
            # 正規化,結果極短的標題列只要碰巧含「大三」就衝到第一,真正列出
            # 大三課程的那一塊反而排到第 14 名。BM25 的 tf 飽和(k1)讓重複出現
            # 有效但不無限加分,長度項(b)則是相對於平均長度而非絕對長度。
            normalized = BM25_K1 * (1 - BM25_B + BM25_B * len(text) / average_length)
            score += _idf(keyword) * frequency * (BM25_K1 + 1) / (frequency + normalized)
        if score <= 0:
            continue
        # 檔名是很強的訊號 —— 問「必修」時檔名有「必選修科目表」的幾乎一定對。
        # 權重壓低是因為 110 份裡有 24 份檔名以「資工系」開頭,給太重會讓
        # 「資工系學士班更換導師申請表」這種不相干的檔案擠進前幾名。
        score += 0.3 * sum(_idf(k) for k in keywords if k in chunk["file"])
        scored.append((score, position))

    scored.sort(key=lambda item: item[0], reverse=True)
    # 直接記位置,不要事後用 chunks.index() 反查 —— 那是 O(n) 查找乘上 n 筆結果
    bm25_order = [position for _, position in scored]

    vector_order = _vector_ranking(query)
    if vector_order is None:
        ranked = bm25_order
    else:
        # Reciprocal Rank Fusion。BM25 分數與餘弦相似度的尺度完全不同,直接加權
        # 相加要先做正規化而且很難調;RRF 只看名次,不必管尺度,而且對其中一路
        # 失準時比較穩健 —— 只要有一路把正確的塊排前面,融合後就還在前面。
        fused = {}
        for rank, position in enumerate(bm25_order[:50]):
            fused[position] = fused.get(position, 0.0) + 1.0 / (RRF_K + rank)
        for rank, position in enumerate(vector_order[:50]):
            fused[position] = fused.get(position, 0.0) + 1.0 / (RRF_K + rank)
        ranked = sorted(fused, key=lambda p: -fused[p])

    if not ranked:
        return "找不到相關文件。"

    parts = []
    for position in ranked[:TOP_K]:
        chunk = chunks[position]
        parts.append(f"[{chunk['file']} 第{chunk['index'] + 1}段]\n{chunk['text']}")
    return "\n\n".join(parts)


ASK_MARKER = "__ASK_USER__"


def ask_clarification(question: str) -> str:
    """在缺少關鍵條件時向使用者追問,而不是猜一個答案。

    這個工具不查任何東西,只是把問題往回傳。`agent.py` 看到 ASK_MARKER 就會
    中止迴圈、把問題交給使用者。

    為什麼需要它:同一個問題在不同屆別/學制下答案不同。實測問「畢業要修幾學分」
    時,模型檢索到 111~115 五屆的科目表,只好回答「共同課程 15 至 16 學分」——
    對任何一個具體的學生來說,這個範圍都是錯的。與其給模糊答案,不如問清楚。
    """
    return f"{ASK_MARKER}{question}"


TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "ask_clarification",
            "description": (
                "當問題缺少決定答案的關鍵條件時,用這個工具向使用者追問,不要自行猜測或給範圍。"
                "必須追問的情況:(1)問到學分、必修、畢業條件但沒說入學學年度(不同屆規定不同);"
                "(2)問到抵免、獎勵但沒說是學士班、碩士班還是博士班;"
                "(3)說「轉進來」但沒說是轉系生還是轉學生(兩者適用條款不同)。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "要問使用者的問題,一次只問一件事,例如「請問你是哪一學年度入學的?」",
                    },
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_documents",
            "description": "搜尋系所行政規定文件(選課、畢業門檻、獎學金、論文格式等),回傳相關文件內容",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜尋關鍵字,例如「畢業學分」「獎學金申請」"},
                },
                "required": ["query"],
            },
        },
    },
]

TOOL_FUNCTIONS = {
    "search_documents": search_documents,
    "ask_clarification": ask_clarification,
}
