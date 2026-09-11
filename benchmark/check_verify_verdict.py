"""確認自我驗證的 verdict 模式與模型呼叫計時,不需要 Ollama。

rewrite 模式要求查核員把整個答案重新輸出一遍,等於每個答案生成兩次。verdict 模式
讓查核員在全部有依據時只回「通過」、沿用草稿。這裡用假的 provider 確認:

1. verdict 模式的解析規則(通過就沿用草稿、要改才採用改寫、理由不外洩)
2. rewrite 模式的行為沒有被改到
3. run_task 會把每次模型呼叫記進 agent.MODEL_CALLS,而且標得出哪一次是自我驗證

用法:
    python benchmark/check_verify_verdict.py
"""

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import agent  # noqa: E402
import tools  # noqa: E402

TASK = "碩士班指導教授什麼時候要選定?"
DRAFT = "指導教授應於碩一第一學期加退選截止前選定。"
CONTEXT = ["[資工系碩士班修業規則_1141120.txt 第2段]\n第五條 碩一第一學期加、退選截止前選定指導教授。"]
FIXED = "指導教授應於碩一第一學期加退選截止前選定。其餘細節文件未提及。"
TIMING = {"prompt_tokens": 100, "prompt_sec": 1.0, "output_tokens": 5, "output_sec": 0.5, "total_sec": 1.6}


class ScriptedProvider:
    """照腳本依序回傳,不碰網路。每個元素是一次 generate() 的 content 或完整結果。"""

    def __init__(self, script):
        self.script = list(script)
        self.systems = []

    def generate(self, messages, tools=None):
        self.systems.append(messages[0]["content"] if messages else "")
        item = self.script.pop(0) if self.script else ""
        result = item if isinstance(item, dict) else {"content": item, "tool_calls": []}
        return {**TIMING, **result}


def verify(mode, reply, context=CONTEXT):
    agent.VERIFY_MODE = mode
    provider = ScriptedProvider([reply])
    with redirect_stdout(io.StringIO()):
        answer = agent.verify_answer(DRAFT, context, provider)
    return answer, provider


def check(name, ok, detail=""):
    print(f"[{'OK  ' if ok else 'FAIL'}] {name}")
    if not ok and detail:
        print(f"        {detail}")
    return ok


def main():
    results = []
    original_mode = agent.VERIFY_MODE
    original_search = tools.TOOL_FUNCTIONS["search_documents"]
    try:
        cases = [
            # (名稱, 模式, 查核員回覆, 預期回傳)
            ("verdict:只回「通過」 -> 沿用草稿", "verdict", "通過", DRAFT),
            ("verdict:「通過。」 -> 沿用草稿", "verdict", "通過。", DRAFT),
            ("verdict:「「通過」」 -> 沿用草稿", "verdict", "「通過」", DRAFT),
            ("verdict:通過後面補理由 -> 沿用草稿,理由不外洩", "verdict", "通過\n所有主張皆有文件依據。", DRAFT),
            ("verdict:回修正版 -> 採用修正版", "verdict", FIXED, FIXED),
            ("verdict:以「通過」開頭的改寫句 -> 採用改寫,不誤判成通過", "verdict",
             "通過審查的部分如下:指導教授應於第一學期選定。", "通過審查的部分如下:指導教授應於第一學期選定。"),
            ("verdict:空白 -> 沿用草稿", "verdict", "", DRAFT),
            ("rewrite:回什麼就用什麼(行為不變)", "rewrite", FIXED, FIXED),
            ("rewrite:空白 -> 沿用草稿(行為不變)", "rewrite", "", DRAFT),
        ]
        for name, mode, reply, expected in cases:
            answer, _ = verify(mode, reply)
            results.append(check(name, answer == expected, f"得到 {answer!r},預期 {expected!r}"))

        _, provider = verify("verdict", "通過")
        results.append(check("verdict 模式送出的是 VERIFY_PROMPT_VERDICT",
                             provider.systems == [agent.VERIFY_PROMPT_VERDICT]))
        _, provider = verify("rewrite", FIXED)
        results.append(check("rewrite 模式送出的仍是原本的 VERIFY_PROMPT",
                             provider.systems == [agent.VERIFY_PROMPT]))
        answer, provider = verify("verdict", "通過", context=[])
        results.append(check("沒有檢索內容時不呼叫模型、直接回傳草稿",
                             answer == DRAFT and provider.systems == []))

        # run_task 全程:查一次 -> 草稿 -> 查核通過
        agent.VERIFY_MODE = "verdict"
        tools.TOOL_FUNCTIONS["search_documents"] = lambda query: CONTEXT[0]
        provider = ScriptedProvider([
            {"content": "", "tool_calls": [
                {"id": "call_0", "name": "search_documents", "arguments": {"query": "指導教授 選定"}}]},
            DRAFT,
            "通過",
        ])
        buf = io.StringIO()
        with redirect_stdout(buf):
            answer = agent.run_task(TASK, provider)
        stages = [c["stage"] for c in agent.MODEL_CALLS]
        results.append(check("run_task 記下三次呼叫:step0、step1、verify",
                             stages == ["step0", "step1", "verify"], f"得到 {stages}"))
        results.append(check("每次呼叫都帶有讀輸入與生成的時間",
                             all(c["prompt_sec"] == 1.0 and c["output_sec"] == 0.5 for c in agent.MODEL_CALLS)))
        results.append(check("查核通過時 run_task 回傳草稿",
                             answer == DRAFT, f"得到 {answer!r}\n--- log ---\n{buf.getvalue()}"))
    finally:
        agent.VERIFY_MODE = original_mode
        tools.TOOL_FUNCTIONS["search_documents"] = original_search

    print(f"\n{sum(results)}/{len(results)} 通過")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
