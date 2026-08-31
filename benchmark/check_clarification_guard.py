"""檢查「駁回多餘追問」的守門規則,不需要 Ollama。

守門規則要同時滿足兩件互相拉扯的事:擋掉題目已經回答過、或不影響答案的追問,
但不能把真正必要的追問一起擋掉 —— 那會退回「共同課程 15 至 16 學分」這種
對任何一個具體學生都是錯的範圍答案。所以正反兩種案例都要測。

案例取自 benchmark v3 的實際輸出(B01、F04 三次執行全部以追問收尾)。

用法:
    python benchmark/check_clarification_guard.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from agent import redundant_clarification  # noqa: E402

# (說明, 使用者的問題, 模型想追問的內容, 是否應該駁回)
CASES = [
    # --- 應該駁回:題目已經回答了 ---
    ("F04 run1:題目已寫明轉學生",
     "我是轉學生,大二轉進資工系,最多可以抵免多少學分?",
     "請問您是轉系生還是轉學生呢?不同類型的轉入可能會有不同的抵免規定。", True),
    ("F04 run2:同上,換句話問",
     "我是轉學生,大二轉進資工系,最多可以抵免多少學分?",
     "請問您是轉系生還是轉學生呢?這兩者的抵免規定不太一樣。", True),
    ("題目已寫明入學學年度",
     "我是113學年度入學的資工系學生,共同課程要修幾學分?",
     "請問您是哪一學年度入學的呢?", True),
    ("題目已寫明學制",
     "我是碩士班學生,最多可以抵免幾學分?",
     "請問您是學士班還是碩士班呢?", True),

    # --- 應該駁回:條件不影響答案 ---
    ("B01 run1:抵免上限與畢業總學分各屆相同",
     "我是轉系生,轉入資工系三年級,最多可以抵免多少學分?畢業總共需要修滿多少學分?",
     "請問您是哪一學年度入學的呢?不同學年度的規定可能有所不同。", True),
    ("B01 run2:同上,換句話問",
     "我是轉系生,轉入資工系三年級,最多可以抵免多少學分?畢業總共需要修滿多少學分?",
     "您是哪一學年度入學的轉系生?不同年度的規定可能會有所不同。", True),

    ("F04 run3:抵免上限依轉入年級,與原科系無關",
     "我是轉學生,大二轉進資工系,最多可以抵免多少學分?",
     "請問您是從哪一個科系轉進來的資工系?", True),

    # --- 不能駁回:這些是必要的追問 ---
    ("共同課程學分確實因屆別而不同,必須問",
     "資工系畢業的共同課程要修幾學分?",
     "請問你是哪一學年度入學的?", False),
    ("通識學分同上",
     "通識領域課程要修多少學分?",
     "請問您是哪一學年度入學的呢?", False),
    ("題目沒說身分別,必須問",
     "我要抵免學分,最多可以抵多少?",
     "請問你是轉系生還是轉學生?", False),
    ("題目沒說學制,必須問",
     "抵免上限是多少?",
     "請問您是學士班還是碩士班?", False),
]


def main():
    failed = 0
    for name, task, question, should_block in CASES:
        reason = redundant_clarification(question, task)
        blocked = reason is not None
        ok = blocked == should_block
        failed += not ok
        print(f"[{'OK  ' if ok else 'FAIL'}] {name}")
        print(f"        追問:{question}")
        print(f"        結果:{'駁回 —— ' + reason if blocked else '放行(交還使用者)'}")
        if not ok:
            print(f"        預期:{'駁回' if should_block else '放行'}")
    print(f"\n{len(CASES) - failed}/{len(CASES)} 通過")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
