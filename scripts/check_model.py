"""檢查某個 Ollama 模型能不能撐起這個 agent。

不是任何模型換上去都能動。這支腳本把四個硬性條件逐一實測,換模型前先跑,
免得把「模型不支援工具呼叫」誤判成「agent 寫壞了」。

用法:
    python scripts/check_model.py qwen2.5:7b
    python scripts/check_model.py llama3.2:3b gemma3:4b        # 可以一次比較多個
"""

import json
import sys
import time
import urllib.error
import urllib.request

# Windows 主控台預設 cp950,連 ✓ ✗ 都印不出來會直接拋 UnicodeEncodeError 中斷。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, __file__.rsplit("scripts", 1)[0])

from tools import TOOLS_SCHEMA, search_documents  # noqa: E402

BASE = "http://localhost:11434"
# agent 實際會用到的長度。檢索一次回傳約 4~9k tokens,模型上限低於這個數字時
# 文件會被截掉,而且 Ollama 不會報錯 —— 這正是本專案踩過的坑。
NEEDED_CTX = 16384


def post(path, payload, timeout=300):
    request = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def check(model: str) -> None:
    print(f"\n{'=' * 58}\n模型: {model}\n{'=' * 58}")

    # 1) 有沒有宣告支援工具呼叫。沒有的話 Ollama 會直接忽略 tools 參數,
    #    agent 每一輪都拿不到 tool_calls,等於完全不會查文件。
    try:
        info = post("/api/show", {"name": model}, timeout=30)
    except urllib.error.HTTPError:
        print("✗ 模型不存在。先執行: ollama pull " + model)
        return
    caps = info.get("capabilities") or []
    has_tools = "tools" in caps
    print(f"{'✓' if has_tools else '✗'} 工具呼叫 (capabilities): {caps}")
    if not has_tools:
        print("  → 這個模型不能用。agent 的每一步都依賴 tool calling。")

    # 2) context 長度夠不夠
    lengths = [v for k, v in (info.get("model_info") or {}).items()
               if k.endswith("context_length")]
    ctx = lengths[0] if lengths else 0
    print(f"{'✓' if ctx >= NEEDED_CTX else '✗'} context 上限: {ctx}(需要 ≥ {NEEDED_CTX})")

    size_gb = (info.get("details", {}).get("parameter_size") or "?")
    print(f"  參數量: {size_gb}")

    if not has_tools:
        return

    # 3) 實際發一次帶 tools 的請求。宣告支援不等於真的會用 —— 有些模型會把
    #    呼叫寫成純文字,agent.py 有救回機制但不是每次都救得回來。
    started = time.time()
    result = post("/api/chat", {
        "model": model, "stream": False,
        "options": {"num_ctx": NEEDED_CTX},
        "tools": TOOLS_SCHEMA,
        "messages": [{"role": "user", "content": "資工系大三的必修有哪些?請查文件。"}],
    })
    calls = (result.get("message") or {}).get("tool_calls") or []
    content = (result.get("message") or {}).get("content") or ""
    leaked = any(m in content for m in ("search_documents", "tool_call", '{"name"'))
    if calls:
        print(f"✓ 實測工具呼叫成功: {calls[0].get('function', {}).get('arguments')}")
    elif leaked:
        print("△ 把工具呼叫寫成純文字(agent.py 的救回機制可處理,但不穩定)")
    else:
        print("✗ 沒有呼叫工具,直接作答 —— 答案不會有文件依據")

    # 4) 長 context 是不是真的吃得下,以及繁簡傾向
    context = search_documents("大三 必修")
    probe = post("/api/chat", {
        "model": model, "stream": False,
        "options": {"num_ctx": NEEDED_CTX},
        "messages": [{"role": "user",
                      "content": f"PASSWORD-7731\n\n{context}\n\n只回答:上面的 PASSWORD 是什麼?"}],
    }, timeout=600)
    answer = (probe.get("message") or {}).get("content") or ""
    ingested = probe.get("prompt_eval_count")
    kept = "7731" in answer
    print(f"{'✓' if kept else '✗'} 長輸入未被截斷: 吃進 {ingested} tokens,標記{'找得到' if kept else '不見了'}")

    try:
        from opencc import OpenCC
        s2tw = OpenCC("s2tw")
        simplified = s2tw.convert(answer) != answer
        print(f"{'△' if simplified else '✓'} 繁簡: 原始輸出{'含簡體(OpenCC 後處理會接住)' if simplified else '未偵測到簡體'}")
    except ImportError:
        pass

    print(f"  本次耗時 {time.time() - started:.1f}s")


if __name__ == "__main__":
    models = sys.argv[1:] or ["qwen2.5:7b"]
    for name in models:
        try:
            check(name)
        except Exception as exc:  # noqa: BLE001
            print(f"✗ 檢查失敗: {type(exc).__name__}: {exc}")
    print("\n判讀:工具呼叫與 context 兩項只要有一個 ✗,這個模型就不能直接換上去。")
