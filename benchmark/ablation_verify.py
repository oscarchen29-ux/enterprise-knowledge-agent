"""測自我驗證步驟到底是幫忙還是幫倒忙。

動機:階梯測試裡好幾題「檢索成功但答錯」,其中「請 3 天病假要證明嗎」直接
回答「文件未提及」—— 而正確答案就在檢索到的第一份文件裡。VERIFY_PROMPT 要求
把沒有文件依據的主張改成「文件未提及」,懷疑它把正確答案也一起砍了。

同一題各跑 N 次,一組開驗證、一組關閉,其餘完全相同。
"""

import io
import sys
from contextlib import redirect_stdout

sys.path.insert(0, __file__.rsplit("benchmark", 1)[0])
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import agent  # noqa: E402
from providers.ollama_provider import OllamaProvider  # noqa: E402

CASES = [
    ("我要請 3 天病假,需要附證明嗎?", ["三日以上", "校醫", "醫院"]),
    ("資工系大三的必修有哪些?", ["計算機組織", "系統程式", "作業系統"]),
    ("生理假一個月可以請幾天?", ["一日", "1日", "一天"]),
    ("休學最多可以休多久?", ["二學年", "2學年", "兩學年"]),
    ("學生宿舍床位怎麼申請?", ["申請", "住宿"]),
]
RUNS = 3


def score(answer: str, keywords: list[str]) -> str:
    if "文件未提及" in answer and not any(k in answer for k in keywords):
        return "拒答"
    return "有內容" if any(k in answer for k in keywords) else "離題"


def run(question: str, provider, verify: bool) -> str:
    original = agent.verify_answer
    if not verify:
        agent.verify_answer = lambda draft, ctx, prov: draft
    try:
        with redirect_stdout(io.StringIO()):
            return agent.run_task(question, provider)
    finally:
        agent.verify_answer = original


def main():
    provider = OllamaProvider(model="qwen2.5:7b")
    totals = {True: {"有內容": 0, "拒答": 0, "離題": 0},
              False: {"有內容": 0, "拒答": 0, "離題": 0}}

    for question, keywords in CASES:
        print(f"\n{question}")
        for verify in (True, False):
            marks = []
            for _ in range(RUNS):
                try:
                    verdict = score(run(question, provider, verify), keywords)
                except Exception as exc:  # noqa: BLE001
                    verdict = f"崩潰({type(exc).__name__})"
                marks.append(verdict)
                totals[verify][verdict] = totals[verify].get(verdict, 0) + 1
            label = "開啟驗證" if verify else "關閉驗證"
            print(f"  {label}: {marks}")

    print("\n" + "=" * 46)
    for verify in (True, False):
        label = "開啟驗證" if verify else "關閉驗證"
        counts = totals[verify]
        total = sum(counts.values())
        print(f"{label}: 有內容 {counts.get('有內容', 0)}/{total}、"
              f"拒答 {counts.get('拒答', 0)}、離題 {counts.get('離題', 0)}")


if __name__ == "__main__":
    main()
