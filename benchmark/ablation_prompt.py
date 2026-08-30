"""測「不要合併相似但不同的規定」這段指示有沒有效。

階梯測試裡有三題失敗都是同一種錯誤 —— 把名稱相近但實質不同的規定混在一起:

  - 問「博士資格考幾科」,答出「研究成果考核」的 SCIE/EI 標準(兩者是不同的考核),
    還把博一必修科目當成考科
  - 問「碩士抵免上限」,答出「三分之二」這個特例,漏掉「二分之一」的通則
  - 問「轉學生抵免」,套用轉系生的條款

同一批題目,舊 prompt 對新 prompt,其餘完全相同。

結論(2026-08-30):**沒有採用這個 prompt 修改。**
自動評分顯示「混淆」由 2 降到 0,看起來有效;但實際讀輸出後發現相反 ——
新 prompt 那次直接把 SCIE/EI 當成資格考的標準,舊 prompt 反而正確指出
「依本系博士候選人資格考核要點辦理」「資格考並不是考幾科」。

問題出在下面的 judge():它只數關鍵字有沒有出現,分不出「提到 SCIE 但加了警語」
與「把 SCIE 當成答案」。這類混淆錯誤本來就需要讀懂語意才判得出來,
關鍵字比對做不到。

保留這支腳本是因為 A/B 的結構可以重用,但**用它之前必須先把 judge 換成人工評分**,
否則它會像這次一樣製造出不存在的改善。
"""

import io
import sys
from contextlib import redirect_stdout

sys.path.insert(0, __file__.rsplit("benchmark", 1)[0])
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import agent  # noqa: E402
from providers.ollama_provider import OllamaProvider  # noqa: E402

EXTRA = (
    "\n\n"
    "回答時務必注意:\n"
    "1. 名稱不同的規定就是不同的規定,不要合併。例如「資格考核」和「研究成果考核」"
    "是兩件事,不可以拿其中一個的內容去回答另一個。\n"
    "2. 如果文件只提到某個規定的名稱、卻沒有它的內容(例如寫著「依本系某某要點辦理」),"
    "要明說那份要點不在可查的文件裡,不要用其他段落的內容湊。\n"
    "3. 同時看到通則和例外時,先講通則,再說明例外適用的條件。"
)

CASES = [
    ("資工系博士班的資格考要考幾科?通過標準是什麼?",
     {"好": ["資格考核要點", "不在", "未提及", "無法", "沒有"],
      "壞": ["SCIE", "EI", "專題研究", "專題討論"]}),
    ("資工系碩士班最多可以抵免幾學分?",
     {"好": ["二分之一", "1/2", "十二", "12"],
      "壞": []}),
    ("我是轉學生,大二轉進資工系,最多可以抵免多少學分?",
     {"好": ["不受", "酌予增加", "轉學生"],
      "壞": ["一年級科目表規定應修學分總數為原則"]}),
]
RUNS = 3


def judge(answer: str, marks: dict) -> str:
    bad = any(k in answer for k in marks["壞"])
    good = any(k in answer for k in marks["好"])
    if bad and not good:
        return "混淆"
    if good and not bad:
        return "正確"
    return "混合" if good else "其他"


def main():
    provider = OllamaProvider(model="qwen2.5:7b")
    baseline = agent.SYSTEM_PROMPT
    totals = {}

    for label, prompt in (("舊 prompt", baseline), ("新 prompt", baseline + EXTRA)):
        agent.SYSTEM_PROMPT = prompt
        counts = {}
        print(f"\n===== {label} =====")
        for question, marks in CASES:
            verdicts = []
            for _ in range(RUNS):
                try:
                    with redirect_stdout(io.StringIO()):
                        answer = agent.run_task(question, provider)
                    verdict = judge(answer, marks)
                except Exception as exc:  # noqa: BLE001
                    verdict = f"崩潰({type(exc).__name__})"
                verdicts.append(verdict)
                counts[verdict] = counts.get(verdict, 0) + 1
            print(f"  {question[:26]:<28} {verdicts}")
        totals[label] = counts
    agent.SYSTEM_PROMPT = baseline

    print("\n" + "=" * 46)
    for label, counts in totals.items():
        total = sum(counts.values())
        print(f"{label}: 正確 {counts.get('正確', 0)}/{total}、"
              f"混合 {counts.get('混合', 0)}、混淆 {counts.get('混淆', 0)}、"
              f"其他 {counts.get('其他', 0)}")


if __name__ == "__main__":
    main()
