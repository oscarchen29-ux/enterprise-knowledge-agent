"""由簡到難的快速探測,用來看系統目前大概在什麼水準。

不是正式 benchmark(那個要重複多次、人工評分),這支只跑一輪,
目的是快速看出「哪一層開始崩」。題目依難度分級,並標註正確答案要點。
"""

import io
import sys
import time

sys.path.insert(0, __file__.rsplit("benchmark", 1)[0])
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import agent  # noqa: E402
from providers.ollama_provider import OllamaProvider  # noqa: E402

PROBES = [
    ("L1 單一事實", "資工系學士班畢業最低要修幾學分?", "128 學分"),
    ("L1 單一事實", "生理假一個月可以請幾天?需要證明嗎?", "每月一日,無需出示證明"),

    ("L2 需選對條款", "我要請 3 天病假,需要附證明嗎?", "需要;三日以上之病假應出具校醫或醫院證明"),
    ("L2 需選對條款", "資工系大三的必修有哪些?", "計算機組織與結構、系統程式、專題一(三上);專題二、作業系統、微算機實驗(三下)"),

    ("L3 屆別相依", "我是113級的資工系學生,全校共同課程和通識各要修幾學分?",
     "113 起為共同 16 學分、通識 15 學分(111/112 是 15/16)"),
    ("L3 跨文件衝突", "資工系碩士班最多可以抵免幾學分?",
     "校規原則為畢業學分二分之一;系規訂最多 12 學分,系規較嚴"),

    ("L4 條款混淆陷阱", "我是轉學生,大二轉進資工系,最多可以抵免多少學分?",
     "轉學生適用另一款,不受轉系生上限限制(答成『不超過一年級應修學分』即為套錯條款)"),
    ("L4 前提錯誤", "我現在大一上學期,想申請轉系,要準備什麼文件?",
     "應指出修業未滿一學年不得申請轉系,而非列出申請流程"),

    ("L5 應拒答", "資工系博士班的資格考要考幾科?通過標準是什麼?", "知識庫沒有此文件,應說文件未提及"),
    ("L5 應拒答", "這學期的加退選截止日是幾月幾號?", "知識庫無學期行事曆,應說文件未提及"),
]


def main():
    provider = OllamaProvider(model="qwen2.5:7b")
    for level, question, expected in PROBES:
        buffer = io.StringIO()
        started = time.time()
        from contextlib import redirect_stdout
        try:
            with redirect_stdout(buffer):
                answer = agent.run_task(question, provider)
        except Exception as exc:  # noqa: BLE001
            answer = f"(崩潰) {type(exc).__name__}: {exc}"
        log = buffer.getvalue()
        searched = "呼叫工具" in log

        print("=" * 70)
        print(f"【{level}】{question}")
        print(f"  應為: {expected}")
        print(f"  查文件: {'是' if searched else '否 ← 沒查'}   {time.time() - started:.1f}s")
        print(f"  回答: {answer.strip()[:340]}")
        print()


if __name__ == "__main__":
    main()
