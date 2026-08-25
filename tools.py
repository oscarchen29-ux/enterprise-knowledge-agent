import os

DOCS_DIR = os.path.join(os.path.dirname(__file__), "docs")

# 之後要換成向量檢索(embedding + Chroma/Milvus)時,只要改這個函式的實作,
# agent.py 跟工具的 JSON Schema 定義都不用動。
def _extract_keywords(query: str) -> list[str]:
    """模型常會丟出連續片語(例如「論文口試前考核」)而不是分開的關鍵字,
    純粹整串比對常常完全比不到,所以額外拆出中文2-gram當作候選關鍵字,
    提高比對到的機率(粗糙但堪用,之後應該換成真正的向量檢索)。"""
    tokens = query.replace(",", " ").replace("、", " ").split()
    bigrams = []
    for token in tokens:
        bigrams.extend(token[i:i + 2] for i in range(len(token) - 1))
    return tokens + bigrams


def search_documents(query: str) -> str:
    """在 docs 資料夾裡用關鍵字比對,回傳相關文件內容片段。"""
    keywords = _extract_keywords(query)
    hits = []

    for filename in os.listdir(DOCS_DIR):
        path = os.path.join(DOCS_DIR, filename)
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        match_count = sum(1 for keyword in keywords if keyword in content)
        if match_count > 0:
            # 用密度(命中數/文件長度)排序，避免長文件(例如21KB的學則)單純因為字多而洗榜，
            # 蓋過真正對題但篇幅較短的文件。
            density = match_count / len(content)
            hits.append((density, f"[{filename}]\n{content}"))

    if not hits:
        return "找不到相關文件。"
    hits.sort(key=lambda x: x[0], reverse=True)
    return "\n\n".join(text for _, text in hits[:3])


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
