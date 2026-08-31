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


# 台灣口語很常用「阿」「啊」當句首發語詞,意思等於「那」——「阿大一呢」就是
# 「那大一呢」。但中國訓練的開源模型不認得這個用法,會把它讀成名字的一部分:
# 實測連問三次「阿大一呢」,模型都反問「請問您要問的是阿大一的必修課程嗎?」。
#
# 這不是檢索或推理的問題,是繁體中文在地用語的問題,而且學生打字時一定會這樣寫。
# 只處理句首、且後面接得上內容的情況,避免誤傷「阿拉伯數字」這類詞。
_COLLOQUIAL_PREFIX = re.compile(r"^[阿啊][\s,,]*(?=[那這大碩博一二三四五六七八九十研學我要想有能可])")


def _normalize(question: str) -> str:
    return _COLLOQUIAL_PREFIX.sub("那", question.strip(), count=1)


def _with_context(question: str, history: dict | None) -> str:
    """把上一輪問答接到接續提問前面,組成一個資訊完整的問題。

    系統本身是無狀態的 —— 每次請求都重跑整個檢索與生成流程。使用者卻會很自然地
    追問「阿大一呢」,那句話單獨看沒有意義:實測檢索其實找對了科目表裡的
    【大一必修科目】那一段,但模型看不懂在問什麼,回答「文件未提及」。

    只帶上一輪(不是整段對話):兩輪以上的歷史會稀釋檢索用的關鍵字,而行政問答
    多半是一問一答加一兩句追問,帶太多反而更糟。

    **只帶上一輪的問題,不帶上一輪的答案。** 一開始兩者都帶,結果模型把前一個答案
    整段複述:問完「114 級大三必修」再問「阿大一呢」,它照抄大三的清單。前一個答案
    在 context 裡份量太重,會蓋過真正的新問題。而且答案本身可能就是錯的,帶著它等於
    讓錯誤延續下去。

    只帶問題就夠了 —— 前一題的「114 學年度入學」交代了身分,「必修」交代了主題,
    接續提問只負責提供改變的部分(大一)。

    改成聊天介面之後,history 是「先前的提問清單」而不是單一問題。只取最近兩則:
    行政問答的脈絡衰減很快(問完必修再問宿舍,必修那題就沒用了),帶太多只會稀釋
    檢索用的關鍵字。
    """
    if not history:
        return question
    if isinstance(history, dict):          # 舊格式,保留相容
        history = [history.get("question") or ""]
    previous = [str(h).strip() for h in history if str(h).strip()][-2:]
    if not previous:
        return question
    joined = "」、「".join(previous)
    return (
        f"使用者先前問過:「{joined}」\n"
        f"現在他接著問:「{question}」\n\n"
        f"請把這兩句合起來理解使用者真正想問什麼,然後只回答接續提問。"
        f"上一個問題裡若已交代身分或入學屆別,接續提問一樣適用,"
        f"不要改用別屆的規定,也不要列出多屆並陳。"
        f"不要重複上一個問題的答案 —— 使用者要問的是新的那一部分。"
        # 沒有這句時,模型會在資訊已經足夠的情況下還去呼叫 ask_clarification,
        # 反問「請問您是想知道 114 學年度大一的必修課程嗎?」—— 它自己都把條件
        # 講出來了,還要使用者再確認一次,等於白跑一趟。SYSTEM_PROMPT 鼓勵追問
        # 是為了第一次提問,接續提問時使用者已經交代過了,不該再問。
        f"使用者已經在上一個問題裡交代過條件了,不要再呼叫 ask_clarification 追問,"
        f"直接查文件並回答。"
    )


@app.route("/api/ask", methods=["POST"])
def ask():
    payload = request.json or {}
    question = _normalize(payload.get("question") or "")
    history = payload.get("history") or None
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
            answer = agent.run_task(_with_context(question, history), _provider())
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


@app.route("/admin/feedback")
def admin_feedback():
    """在瀏覽器上看回報。必須用 --admin-token 啟動,而且網址要帶對 token。

    預設關閉是刻意的:網頁會透過內網穿透開給全系使用,而回報裡有學生的真實提問,
    不能讓任何人打開網址就看得到。沒設 token 時這個路徑一律回 404,連存在都不透露。
    """
    token = app.config.get("ADMIN_TOKEN")
    if not token or request.args.get("token") != token:
        return "Not Found", 404

    rows = []
    if os.path.exists(FEEDBACK_PATH):
        with io.open(FEEDBACK_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        pass
    rows.reverse()          # 新的在上面
    return render_template("feedback.html", rows=rows, total=len(rows))


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
    parser.add_argument("--admin-token", default=None,
                        help="設了才會開放 /admin/feedback?token=... 檢視回報;不設就完全關閉")
    args = parser.parse_args()

    app.config["MODEL"] = args.model
    app.config["ADMIN_TOKEN"] = args.admin_token
    _install_source_probe()

    print(f"知識庫 {len(tools._load_chunks())} 塊,向量索引 "
          f"{'已載入' if tools._load_index() else '未啟用(僅 BM25)'}")
    print(f"模型 {args.model}    http://{args.host}:{args.port}")
    if args.admin_token:
        print(f"回報檢視  http://{args.host}:{args.port}/admin/feedback?token={args.admin_token}")
    else:
        print("回報檢視  未開放(要開放請加 --admin-token,或用 "
              "python scripts/show_feedback.py 在本機看)")
    # threaded=True 讓排隊中的請求不會卡住整個伺服器,實際的模型呼叫仍由鎖串行化
    app.run(host=args.host, port=args.port, threaded=True)
