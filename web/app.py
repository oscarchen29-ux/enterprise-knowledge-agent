"""給系上同學試用的網頁介面。

三個設計要求,都不是為了好看:

1. **一定要顯示出處。** 這個系統會答錯,而且最危險的錯是「有出處、看起來專業、
   實質答錯」——例如把博士班的「研究成果考核」當成「資格考」回答。使用者只有
   看得到原文片段,才有辦法自己識破。出處是安全機制,不是裝飾。

2. **明講這不是官方系統。** 答錯會讓學生錯過期限或延畢,框架必須是「幫我測試」
   而不是「這裡有答案」。

3. **要能回報。** 真實提問與錯誤回報正是這個專題最缺的評估資料 ——
   自己出的考題永遠測不到學生真正會問的東西。

執行:
    python web/app.py                    # http://127.0.0.1:5000
    python web/app.py --host 0.0.0.0     # 開放區網/穿透存取
"""

import argparse
import datetime
import io
import json
import os
import re
import sys
import threading
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from flask import Flask, jsonify, render_template, request  # noqa: E402

import agent  # noqa: E402
import tools  # noqa: E402
from providers.ollama_provider import OllamaProvider  # noqa: E402

app = Flask(__name__)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEEDBACK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "feedback.jsonl")

# 8GB 顯卡一次只跑得動一個請求。沒有這道鎖的話,兩個人同時問會讓 Ollama 反覆
# 換入換出模型,兩邊都變慢甚至逾時。排隊比較慢但可預期。
_MODEL_LOCK = threading.Lock()
_WAITING = 0
_WAITING_LOCK = threading.Lock()

_PROVIDER = None
_SOURCES = []           # 側錄本次查詢取回的片段
_ANSWERS = {}           # answer_id -> 回報時要一起存的內容


def _install_source_probe():
    """側錄 search_documents 取回的片段,好把出處顯示給使用者。

    用 monkey-patch 而不是改 agent.py:回傳型別維持字串,CLI 與 benchmark
    都不受影響。benchmark/run_benchmark.py 也是用同樣的方式側錄。
    """
    original = tools.search_documents

    def logged(query):
        result = original(query)
        for block in result.split("\n\n"):
            match = re.match(r"^\[(.+?\.txt)(?:\s+第(\d+)段)?\]\n(.*)$", block, re.S)
            if match:
                _SOURCES.append({
                    "file": match.group(1),
                    "segment": match.group(2),
                    "text": match.group(3).strip(),
                })
        return result

    tools.TOOL_FUNCTIONS["search_documents"] = logged


def _provider():
    global _PROVIDER
    if _PROVIDER is None:
        _PROVIDER = OllamaProvider(model=app.config["MODEL"])
    return _PROVIDER


@app.route("/")
def index():
    return render_template("index.html", model=app.config["MODEL"])


@app.route("/api/ask", methods=["POST"])
def ask():
    question = (request.json or {}).get("question", "").strip()
    if not question:
        return jsonify({"error": "請輸入問題"}), 400
    if len(question) > 300:
        return jsonify({"error": "問題太長,請縮短到 300 字以內"}), 400

    # 條件不足時直接回追問,不佔用模型 —— 這條路不需要排隊
    clarify = agent.needs_clarification(question)
    if clarify:
        return jsonify({"type": "clarify", "question": clarify, "original": question})

    global _WAITING
    with _WAITING_LOCK:
        _WAITING += 1
        ahead = _WAITING - 1

    started = time.time()
    try:
        with _MODEL_LOCK:
            _SOURCES.clear()
            answer = agent.run_task(question, _provider())
            sources = list(_SOURCES)
    except Exception as exc:  # noqa: BLE001
        app.logger.exception("run_task failed")
        return jsonify({"error": f"系統發生錯誤({type(exc).__name__})。請再試一次。"}), 500
    finally:
        with _WAITING_LOCK:
            _WAITING -= 1

    # 模型自己呼叫 ask_clarification 時也是追問,不是答案。這條路徑沒有被
    # needs_clarification 攔下來(確定性規則只涵蓋屆別與抵免身分兩種情況),
    # 若不分辨,網頁會把追問當答案顯示、還套上「沒有查到任何文件」的紅字警告 ——
    # 對使用者是誤導,而且沒有地方可以回答。
    if agent.LAST_WAS_CLARIFICATION:
        return jsonify({"type": "clarify", "question": answer, "original": question,
                        "elapsed": round(time.time() - started, 1)})

    answer_id = uuid.uuid4().hex[:12]
    _ANSWERS[answer_id] = {"question": question, "answer": answer,
                           "sources": [s["file"] for s in sources]}
    # 只留最近 500 筆,避免長時間運行後記憶體無限成長
    if len(_ANSWERS) > 500:
        for key in list(_ANSWERS)[:-500]:
            _ANSWERS.pop(key, None)

    return jsonify({
        "type": "answer",
        "answer_id": answer_id,
        "answer": answer,
        "sources": sources,
        "searched": bool(sources),
        "elapsed": round(time.time() - started, 1),
        "queued_ahead": ahead,
    })


@app.route("/api/feedback", methods=["POST"])
def feedback():
    payload = request.json or {}
    answer_id = payload.get("answer_id", "")
    verdict = payload.get("verdict", "")
    if verdict not in ("correct", "wrong", "unclear"):
        return jsonify({"error": "unknown verdict"}), 400

    record = {
        "time": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "verdict": verdict,
        "comment": (payload.get("comment") or "")[:1000],
        "model": app.config["MODEL"],
    }
    record.update(_ANSWERS.get(answer_id, {"question": "(已逾期,未留存)"}))
    with io.open(FEEDBACK_PATH, "a", encoding="utf-8", newline="") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return jsonify({"ok": True})


@app.route("/api/health")
def health():
    return jsonify({
        "model": app.config["MODEL"],
        "chunks": len(tools._load_chunks()),
        "vector_index": bool(tools._load_index()),
        "waiting": _WAITING,
    })


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--model", default="qwen2.5:7b")
    args = parser.parse_args()

    app.config["MODEL"] = args.model
    _install_source_probe()

    print(f"知識庫 {len(tools._load_chunks())} 塊,向量索引 "
          f"{'已載入' if tools._load_index() else '未啟用(僅 BM25)'}")
    print(f"模型 {args.model}    http://{args.host}:{args.port}")
    # threaded=True 讓排隊中的請求不會卡住整個伺服器,實際的模型呼叫仍由鎖串行化
    app.run(host=args.host, port=args.port, threaded=True)
