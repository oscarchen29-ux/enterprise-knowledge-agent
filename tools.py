import os

DOCS_DIR = os.path.join(os.path.dirname(__file__), "docs")

# 之後要換成向量檢索(embedding + Chroma/Milvus)時,只要改這個函式的實作,
# agent.py 跟工具的 JSON Schema 定義都不用動。
def search_documents(query: str) -> str:
    """在 sample_docs 資料夾裡用關鍵字比對,回傳相關文件內容片段。"""
    keywords = query.replace(",", " ").split()
    hits = []

    for filename in os.listdir(DOCS_DIR):
        path = os.path.join(DOCS_DIR, filename)
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        if any(keyword in content for keyword in keywords):
            hits.append(f"[{filename}]\n{content}")

    if not hits:
        return "找不到相關文件。"
    return "\n\n".join(hits)


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
