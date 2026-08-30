import math
import os
import re

DOCS_DIR = os.path.join(os.path.dirname(__file__), "docs")

# 切塊大小。太小會把一條法規切散、答案讀不完整;太大則退回原本「長文件塞爆 context」
# 的問題。600 字約可容納一到兩條完整條文,實測是可用的折衷。
CHUNK_SIZE = 600
CHUNK_OVERLAP = 120      # 讓被切在邊界上的句子在兩塊裡都出現,避免答案剛好被切斷
TOP_K = 6                # 回傳幾塊。塊比整份文件小很多,可以多給幾塊而不爆 context

# BM25 參數。k1 控制詞頻飽和速度,b 控制長度正規化強度,兩者都是文獻慣用值。
BM25_K1 = 1.5
BM25_B = 0.75

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

    for chunk in chunks:
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
        scored.append((score, chunk))

    if not scored:
        return "找不到相關文件。"

    scored.sort(key=lambda item: item[0], reverse=True)
    parts = []
    for _, chunk in scored[:TOP_K]:
        parts.append(f"[{chunk['file']} 第{chunk['index'] + 1}段]\n{chunk['text']}")
    return "\n\n".join(parts)


TOOLS_SCHEMA = [
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
}
