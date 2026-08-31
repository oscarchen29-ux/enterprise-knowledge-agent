"""確認模型回傳空白內容時,agent 會先強制檢索再放棄,不需要 Ollama。

原本的行為是空白就直接回傳失敗訊息,連保底檢索都不做。benchmark v3 剩下的
靜默失敗(C01、D05、B01)全部走這條路。實際跑模型測不到這個修正 —— 空白是
偶發的,跑六次可能一次都沒遇到 —— 所以用假的 provider 強制觸發。

用法:
    python benchmark/check_blank_recovery.py
"""

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import agent  # noqa: E402

TASK = "碩士班指導教授什麼時候要選定?"


class ScriptedProvider:
    """照腳本依序回傳,不碰網路。每個元素是一次 generate() 的結果。"""

    def __init__(self, script):
        self.script = list(script)
        self.seen = []

    def generate(self, messages, tools=None):
        self.seen.append(messages[-1]["content"] if messages else "")
        return self.script.pop(0) if self.script else {"content": "", "tool_calls": []}


def blank():
    return {"content": "", "tool_calls": []}


def check(name, script, expect_fail_msg, expect_fallback):
    provider = ScriptedProvider(script)
    original = agent.verify_answer
    agent.verify_answer = lambda draft, ctx, prov: draft  # 驗證步驟會再打一次模型
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            answer = agent.run_task(TASK, provider)
    finally:
        agent.verify_answer = original
    log = buf.getvalue()

    got_fallback = "強制檢索一次後重新回答" in log
    got_fail = answer.startswith("模型未產生有效回答")
    ok = (got_fallback == expect_fallback) and (got_fail == expect_fail_msg)
    print(f"[{'OK  ' if ok else 'FAIL'}] {name}")
    print(f"        有沒有保底檢索:{got_fallback}(預期 {expect_fallback})")
    print(f"        是不是失敗訊息:{got_fail}(預期 {expect_fail_msg})")
    if not ok:
        print("        --- log ---\n" + log)
    return ok


def main():
    results = [
        # 第一次空白 -> 應該強制檢索,第二次給得出內容 -> 正常作答
        check("空白之後保底檢索,第二次答得出來",
              [blank(), {"content": "指導教授應於第一學期加退選截止前選定。", "tool_calls": []}],
              expect_fail_msg=False, expect_fallback=True),
        # 連續空白 -> 保底檢索過了還是空白,才回傳失敗訊息
        check("餵過文件仍然空白,才放棄",
              [blank(), blank()],
              expect_fail_msg=True, expect_fallback=True),
    ]
    print(f"\n{sum(results)}/{len(results)} 通過")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
