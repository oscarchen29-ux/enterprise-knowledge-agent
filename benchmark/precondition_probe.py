"""量測「模型知不知道該問學生什麼」——不需要 agent 迴圈,也不需要模擬學生。

為什麼這支程式值得單獨存在:
研究問題的核心是「Agent 能不能從法規推導出使用者還缺什麼」。這件事可以用
**單次提示**量到,不必先把多輪對話、表單填寫、模擬學生全部做出來。先量這一步,
如果模型根本列不出該問什麼,後面做再多迴圈設計也沒有意義。

作法:
    給模型「法規全文 + 申請表全文 + 學生的開場白」,要它輸出
    「資格判定」與「還需要問學生什麼」,再跟 forms/ 裡的人工標註比對。

四個指標(全部用程式判定,不用 LLM 當裁判,理由同 inclusion_accuracy.py):
    漏問率        must_ask 裡沒被問到的比率
    冗餘提問率    問了 must_not_ask(開場白已經講過的)的比率
    資格判定正確率 最終判定是否等於標註的 verdict
    告知完整率    must_tell 裡有多少被主動講出來

把自由文字對應到前置條件 id,靠的是下面 _PATTERNS 的關鍵字規則。這是確定性的、
看得懂的,壞處是改標註時要跟著改規則 —— 相較於用另一個 LLM 判讀,這個代價值得。

斷線續跑:
    實驗室 Jetson 透過 SSH 通道存取,通道會自己斷(2026-09-23~24 兩天斷四次,
    浪費 74 次執行)。本程式預設**續跑**:每題一寫入,重跑時自動跳過 out 檔裡
    已經有紀錄的 (模型, 題號),所以斷線最多只損失正在跑的那一題。
    連線失敗會自動重試,重試用盡才記成待補,不會中止整批。

用法:
    python benchmark/precondition_probe.py                        # 預設 qwen2.5:14b
    python benchmark/precondition_probe.py --models qwen2.5:7b qwen2.5:14b
    python benchmark/precondition_probe.py --host http://localhost:11435 --models gemma4:31b
    python benchmark/precondition_probe.py --cases T01 T05 T17     # 只跑特定題
    python benchmark/precondition_probe.py --out X.jsonl --fresh   # 不續跑,整批重來
    python benchmark/precondition_probe.py --repeats 3             # 同一題跑三次量變異
"""

import argparse
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FORMS_DIR = os.path.join(ROOT, "forms")
DOCS = os.path.join(ROOT, "docs")

sys.path.insert(0, HERE)
from forms_config import FORMS  # noqa: E402

_PROMPT_HEAD = """你是學校的行政助理。學生想辦理一項申請,你要依據下面的官方文件判斷。

{documents}
【學生說】
{opening}
"""

_PROMPT_TAIL = """
請只輸出 JSON,不要有其他文字,也不要用 markdown 的程式碼圍欄:
{{
  "資格判定": "{verdict_options}",
  "理由": "引用文件中的具體條件與數字",
  "可獲得": "金額、床位類別或補助項目,沒有就寫無",
  "還需要問學生的資訊": ["你還需要學生提供哪些資訊才能定案,逐項列出", "..."],
  "必須告知學生的事項": ["學生不會自己問、但你一定要講的事,逐項列出", "..."]
}}

注意:
- 只依據上面的文件,文件沒寫的不要編。
- 文件如果互相矛盾、或根本沒有規定,要在「必須告知學生的事項」裡指出來,不要自己補一個答案。
- 學生已經說過的事不要再問。"""

# 三個提示法變體。要回答的問題是:漏問是「做不到」還是「沒叫它做」?
#
#   base      目前的提示法,基準線。
#   checklist 強迫它先把法規要求的條件逐條列出、再標示學生講了哪些 —— 針對
#             「根本沒想到要問」這個失敗模式(qwen2.5:7b 二十題全部交白卷)。
#   separate  明確區分「向學生取得的事實」與「請學生準備的文件」—— 針對
#             14b 把應檢附文件填進「還需要問」的失敗模式(20 題中有 5 題)。
#
# 如果 checklist 把漏問率救起來,代表那是提示的問題,不是模型能力的上限;
# 如果救不起來,就是能力上限 —— 這個區別直接決定後面要往哪個方向改。
_VARIANT_INSTRUCTION = {
    "base": "",
    "checklist": """
在回答之前,請先在心裡依序做完這三步(不要輸出過程,只輸出最後的 JSON):
1. 把文件中規定的「要完成這項申請必須滿足或提供的條件」逐條列出,包含資格條件、
   排除條件(例如限領一次、已受處分)、期限、以及應檢附的文件。
2. 逐條對照學生說的話,標示每一條是「學生已經講了」、「學生沒講」還是「與學生無關」。
3. 所有「學生沒講」而且會影響判定結果的條件,全部放進「還需要問學生的資訊」。
""",
    "separate": """
「還需要問學生的資訊」與「必須告知學生的事項」要嚴格區分:
- 「還需要問學生的資訊」只放**你要向學生取得的事實**(例如他考幾分、是不是低收入戶、
  有沒有領過)。這些是你不問就不知道、而且會改變判定結果的事。
- 應檢附的文件(學生證、成績證明、收據等)是**請學生去準備**的,
  屬於「必須告知學生的事項」,不要放進「還需要問學生的資訊」。
""",
}


def build_prompt(variant, documents, verdict_options, opening):
    return (_PROMPT_HEAD.format(documents=documents, opening=opening)
            + _VARIANT_INSTRUCTION[variant]
            + _PROMPT_TAIL.replace("{{", "{").replace("}}", "}")
                          .replace("{verdict_options}", verdict_options))


def load_documents(cfg):
    """把設定裡列的法規讀成提示用的文字區塊。只取【條文】之後,前面是來源資訊。"""
    parts = []
    for label, name in cfg["docs"]:
        text = io.open(os.path.join(DOCS, name), encoding="utf-8").read()
        body = text.split("【條文】", 1)[1].strip() if "【條文】" in text else text.strip()
        parts.append("【文件:" + label + "】\n" + body + "\n")
    return "\n".join(parts)


RETRY_LIMIT = 5
RETRY_WAIT_SEC = 20


def ask(host, model, prompt, timeout=900, seed=None, think=None):
    """送一次生成請求。連線類錯誤會重試 —— SSH 通道斷掉時這是唯一的救命繩。

    HTTPError 不重試:那是模型或請求本身的問題(例如 context 爆掉),重試只是浪費時間。
    """
    options = {"temperature": 0, "num_ctx": 16384}
    if seed is not None:
        options["seed"] = seed
    body = {"model": model, "prompt": prompt, "stream": False, "options": options}
    # 思考型模型(例如 qwen3.8:27b)預設會先輸出一長串思考才給答案。實測在本專案的
    # 提示上,關掉思考快約 5 倍而答案相同;但「思考有沒有幫助」本身是可比較的設定,
    # 所以留成參數而不是寫死。think=None 表示不送這個欄位,維持模型預設。
    if think is not None:
        body["think"] = think
    payload = json.dumps(body).encode("utf-8")
    last = None
    for attempt in range(1, RETRY_LIMIT + 1):
        request = urllib.request.Request(f"{host}/api/generate", data=payload,
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)["response"]
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            if attempt < RETRY_LIMIT:
                print(f"      連線失敗({exc}),{RETRY_WAIT_SEC}s 後第 {attempt + 1} 次嘗試")
                time.sleep(RETRY_WAIT_SEC)
    raise last


def parse(raw):
    """模型常常在 JSON 外面包 markdown 圍欄或加一句廢話,這裡盡量救回來。"""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.S)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            return None
    return None


def matched_ids(items, wanted, patterns):
    """模型列出來的句子裡,命中了哪些條件 id。沒有對應規則的 id 一律算沒命中。"""
    blob = " ".join(items)
    return {i for i in wanted if i in patterns and re.search(patterns[i], blob)}


def redundant_hits(asked, forbidden, patterns):
    blob = " ".join(asked)
    return [k for k in forbidden if k in patterns and re.search(patterns[k], blob)]


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--variant", default="base",
                        choices=["base", "checklist", "separate"],
                        help="提示法變體。checklist=強迫逐條檢查;separate=明確區分問資訊與要文件")
    parser.add_argument("--form", default="英語檢測獎勵", choices=sorted(FORMS),
                        help="要評測哪一張表單的程序")
    parser.add_argument("--models", nargs="+", default=["qwen2.5:14b"])
    parser.add_argument("--host", default="http://localhost:11434")
    parser.add_argument("--cases", nargs="*", help="只跑這幾題,預設全跑")
    parser.add_argument("--out", default=None,
                        help="逐題輸出的 jsonl 檔(預設寫到 benchmark/runs/)")
    parser.add_argument("--think", choices=["on", "off"], default=None,
                        help="思考型模型是否開啟思考。不指定就用模型預設")
    parser.add_argument("--fresh", action="store_true",
                        help="不續跑,整批重來(預設會跳過 out 檔裡已完成的題目)")
    parser.add_argument("--repeats", type=int, default=1,
                        help="同一題跑幾次。跑一次的數字是噪音,要談差異至少跑三次")
    args = parser.parse_args()

    cfg = FORMS[args.form]
    documents = load_documents(cfg)
    cases = json.load(io.open(os.path.join(FORMS_DIR, cfg["cases"]),
                              encoding="utf-8"))["cases"]
    if args.cases:
        cases = [c for c in cases if c["id"] in args.cases]

    stamp = time.strftime("%Y%m%d_%H%M%S")
    runs_dir = os.path.join(HERE, "runs")
    os.makedirs(runs_dir, exist_ok=True)
    slug = args.form
    out_path = args.out or os.path.join(runs_dir, f"precondition_probe_{slug}_{stamp}.jsonl")

    # 續跑:已經寫進 out 檔的 (模型, 題號, 第幾次) 直接跳過。斷線最多損失正在跑的那一題。
    done = set()
    if os.path.exists(out_path) and not args.fresh:
        for line in io.open(out_path, encoding="utf-8"):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue          # 斷電寫到一半的行,忽略
            if not rec.get("error"):
                done.add((rec.get("variant", "base"), rec.get("model"), rec.get("id"), rec.get("run", 1)))
        if done:
            print(f"續跑:{out_path} 已有 {len(done)} 筆完成的紀錄,將跳過")
    sink = io.open(out_path, "a" if done else "w", encoding="utf-8")

    summary = []
    for model in args.models:
        print("=" * 72)
        print(f"表單:{args.form}  提示法:{args.variant}  模型:{model}  題數:{len(cases)} x {args.repeats} 次")
        print("=" * 72)
        ask_need = ask_hit = tell_need = tell_hit = 0
        redundant = verdict_right = parsed = 0

        pending = [(c, r) for r in range(1, args.repeats + 1) for c in cases
                   if (args.variant, model, c["id"], r) not in done]
        if not pending:
            print("  這個模型的題目都已完成,跳過")
        for case, run_no in pending:
            started = time.time()
            tag = f"{case['id']}" + (f"#{run_no}" if args.repeats > 1 else "")
            try:
                raw = ask(args.host, model,
                          build_prompt(args.variant, documents,
                                       cfg["verdict_options"], case["opening"]),
                          seed=run_no,
                          think=None if args.think is None else (args.think == "on"))
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                print(f"  [{tag}] 重試用盡,待補:{exc}")
                continue
            except urllib.error.HTTPError as exc:
                print(f"  [{tag}] 伺服器錯誤:{exc}")
                continue
            data = parse(raw)
            elapsed = time.time() - started
            if data is None:
                print(f"  [{tag}] 輸出不是合法 JSON({elapsed:.0f}s)")
                sink.write(json.dumps({"form": args.form, "variant": args.variant,
                                       "model": model, "id": case["id"], "run": run_no,
                                       "error": "unparsable", "raw": raw[:2000]},
                                      ensure_ascii=False) + "\n")
                continue
            parsed += 1

            asked = [str(x) for x in (data.get("還需要問學生的資訊") or [])]
            told = [str(x) for x in (data.get("必須告知學生的事項") or [])]

            got_ask = matched_ids(asked, case["must_ask"], cfg["ask"])
            missed = [p for p in case["must_ask"] if p not in got_ask]
            got_tell = matched_ids(asked + told, case["must_tell"], cfg["ask"])
            missed_tell = [p for p in case["must_tell"] if p not in got_tell]
            dup = redundant_hits(asked, case["must_not_ask"], cfg["redundant"])
            ok = cfg["verdict_ok"](data.get("資格判定"), case)

            ask_need += len(case["must_ask"]); ask_hit += len(got_ask)
            tell_need += len(case["must_tell"]); tell_hit += len(got_tell)
            redundant += len(dup)
            verdict_right += int(ok)

            flags = []
            if missed:      flags.append(f"漏問 {','.join(missed)}")
            if dup:         flags.append(f"冗餘 {','.join(dup)}")
            if missed_tell: flags.append(f"沒告知 {','.join(missed_tell)}")
            mark = "O" if ok else "X"
            print(f"  [{tag}] 判定{mark} {data.get('資格判定','?'):<5}"
                  f" {'  '.join(flags) if flags else '全中'}  ({elapsed:.0f}s)")

            sink.write(json.dumps({
                "form": args.form, "variant": args.variant, "model": model,
                "think": args.think, "run": run_no,
                "id": case["id"], "opening": case["opening"],
                "verdict_got": data.get("資格判定"), "verdict_ok": ok,
                "理由": data.get("理由"), "可獲得": data.get("可獲得"),
                "asked": asked, "told": told,
                "must_ask": case["must_ask"], "missed_ask": missed,
                "must_tell": case["must_tell"], "missed_tell": missed_tell,
                "redundant": dup, "elapsed": round(elapsed, 1),
            }, ensure_ascii=False) + "\n")
            sink.flush()

        n = parsed or 1
        summary.append({
            "model": f"{model} [{args.variant}]", "題數": len(cases) * args.repeats, "可解析": parsed,
            "資格判定正確率": verdict_right / n,
            "漏問率": 1 - (ask_hit / ask_need) if ask_need else 0,
            "冗餘提問數": redundant,
            "告知完整率": tell_hit / tell_need if tell_need else 0,
        })
        print()

    sink.close()
    print("=" * 72)
    print(f"{'模型 [提示法]':<26}{'可解析':>8}{'資格判定':>10}{'漏問率':>10}{'冗餘提問':>10}{'告知完整':>10}")
    for s in summary:
        print(f"{s['model']:<26}{s['可解析']:>5}/{s['題數']:<2}"
              f"{s['資格判定正確率']:>9.1%}{s['漏問率']:>10.1%}"
              f"{s['冗餘提問數']:>10}{s['告知完整率']:>10.1%}")
    print(f"\n逐題紀錄:{out_path}")
    print("注意:漏問率是以關鍵字規則比對的,規則寫得寬,所以這個數字是漏問的**下界**。")


if __name__ == "__main__":
    main()
