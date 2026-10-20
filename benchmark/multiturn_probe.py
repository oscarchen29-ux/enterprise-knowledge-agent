# -*- coding: utf-8 -*-
"""多輪評測:模型問了問題之後,由模擬學生依隱藏檔案回答,再看最終判定對不對。

為什麼需要這一支(單輪量不到的東西):
`precondition_probe.py` 是單輪的 —— 模型就算正確意識到「我還缺資訊」,
也沒有人可以回答它,只能硬給一個判定。實測結果就是這樣:提示法改成 `separate`
之後 qwen2.5:14b 的漏問率從 89.3% 降到 46.4%,**資格判定卻從 65.4% 降到 61.5%**。
在單輪設計裡,「漏問率」與「資格判定正確率」在結構上互相拉扯,
所以單輪量不出「主動反問到底有沒有用」。

這一支讓對話繼續下去,量的是真正要回答的問題:
**Agent 自己決定去問,最後答得比較對嗎?要多問幾輪才換到那個正確率?**

## 模擬學生是查表,不是 LLM

每個案例有一份隱藏檔案(`forms/*_測試案例.json` 的 `profile`)。
模型問一句話,用 `forms_config.py` 的 `student` 正規表示式判斷它在問哪個欄位:

  * 對應到欄位且檔案裡有 -> 回答該值
  * 對應到欄位但檔案裡沒有 -> 回「這個我不知道」
  * 對應不到任何欄位 -> 回「抱歉,我不太確定你在問什麼」,並計入 `unmatched`

**不用 LLM 扮演學生**,理由同 `inclusion_accuracy.py` 不用 LLM 當裁判:
會引入第二層不確定性,而且同一題每次回答不同,實驗不可重現。
代價是模型用了規則沒涵蓋的問法時會被判成「聽不懂」—— 這是評測的已知失真,
所以 `unmatched` 一定要回報,不能藏起來。

用法:
    python benchmark/multiturn_probe.py --form 英語檢測獎勵 --models qwen2.5:14b
    python benchmark/multiturn_probe.py --form 宿舍床位申請 --max-turns 5 --variant separate
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
from forms_config import (FORMS, STUDENT_FALLBACK, DOCUMENT_REPLY,  # noqa: E402
                          UNKNOWN_REPLY, UNMATCHED_REPLY)
from precondition_probe import (ask, parse, load_documents,       # noqa: E402
                                matched_ids, redundant_hits,
                                _VARIANT_INSTRUCTION)

FIRST_TURN = """你是學校的行政助理。學生想辦理一項申請,你要依據下面的官方文件判斷。

{documents}
【學生說】
{opening}
{variant}
請只輸出 JSON,不要有其他文字,也不要用 markdown 的程式碼圍欄:
{{
  "狀態": "需要更多資訊 或 可以定案",
  "要問學生的問題": ["若狀態為「需要更多資訊」,把你要問的問題逐句列出;可以定案就留空陣列"],
  "資格判定": "{verdict_options}(狀態為「需要更多資訊」時填「尚未定案」)",
  "理由": "引用文件中的具體條件與數字",
  "可獲得": "金額、床位類別或補助項目,沒有就寫無",
  "必須告知學生的事項": ["學生不會自己問、但你一定要講的事"]
}}

注意:
- 只依據上面的文件,文件沒寫的不要編。
- 文件如果互相矛盾、或根本沒有規定,要在「必須告知學生的事項」裡指出來。
- 學生已經說過的事不要再問。
- 你有機會追問,但**追問與定案兩者同等重要**:資訊足夠就直接定案,不足才提問。
- **只問文件裡真的有規定的條件。** 文件沒有規定的事項不要問,
  例如文件只在特定條款提到低收入戶,就不要去問家庭收入、開支等文件未規定的事。
- 若現有資訊已足以得出「不符合」的結論,請直接定案,不要再找其他理由。"""

FORCE_TURN = """對話已經結束,學生不會再回答任何問題了。

請依據目前已知的全部資訊**現在就給出最終判定**,不可以再說「尚未定案」。
如果資訊仍有缺口,就在「理由」裡說明缺什麼、以及在該缺口下你的最佳判斷是什麼。

只輸出 JSON:
{{
  "狀態": "可以定案",
  "要問學生的問題": [],
  "資格判定": "{verdict_options}",
  "理由": "引用文件中的具體條件與數字",
  "可獲得": "金額、床位類別或補助項目,沒有就寫無",
  "必須告知學生的事項": ["學生不會自己問、但你一定要講的事"]
}}"""


NEXT_TURN = """學生回答了你的問題:

{answers}

現在請重新判斷。如果資訊已經足夠,把狀態設為「可以定案」並給出資格判定。
只有在**文件明文規定、而且會改變判定結果**的條件還沒問到時才繼續追問。
不要重複問已經回答過的事;學生答不出來的事不要再問第二次;
文件沒有規定的事項一律不要問。

只輸出 JSON,不要有其他文字:
{{
  "狀態": "需要更多資訊 或 可以定案",
  "要問學生的問題": ["還要問的問題;可以定案就留空陣列"],
  "資格判定": "{verdict_options}(狀態為「需要更多資訊」時填「尚未定案」)",
  "理由": "引用文件中的具體條件與數字",
  "可獲得": "金額、床位類別或補助項目,沒有就寫無",
  "必須告知學生的事項": ["學生不會自己問、但你一定要講的事"]
}}

**每一輪都要完整輸出以上六個欄位,不可省略「資格判定」。**"""


def student_reply(question, profile, patterns):
    """模擬學生:查表回答。回傳 (回覆文字, 命中的欄位或 None)。

    一題問句可能同時命中多個樣式(例如「成績」同時像分數也像等級),
    所以先收集所有命中的欄位,優先回答檔案裡真的有的那一個;
    都沒有才用 STUDENT_FALLBACK 的退路;再沒有才說不知道。
    """
    hits = [k for k, pat in patterns.items() if re.search(pat, question)]
    if not hits:
        return UNMATCHED_REPLY, None
    if all(k == "_文件" for k in hits):
        return DOCUMENT_REPLY, "_文件"
    hits = [k for k in hits if k != "_文件"]
    for k in hits:
        if k in profile:
            return f"{k}:{profile[k]}", k
    for k in hits:
        for alt in STUDENT_FALLBACK.get(k, []):
            if alt in profile:
                return f"{alt}:{profile[alt]}", alt
    return f"關於{hits[0]}:{UNKNOWN_REPLY}", hits[0]


def run_case(host, model, cfg, documents, case, variant, max_turns, timeout,
             force_choice=False):
    """跑完一題的多輪對話,回傳整段紀錄。"""
    profile = case["profile"]
    student_patterns = cfg["student"]
    transcript, asked_all = [], []
    unmatched = repeated = 0
    prompt = FIRST_TURN.format(documents=documents, opening=case["opening"],
                               variant=_VARIANT_INSTRUCTION[variant],
                               verdict_options=cfg["verdict_options"])
    data = None
    for turn in range(1, max_turns + 1):
        raw = ask(host, model, prompt, timeout=timeout)
        data = parse(raw)
        if data is not None and not data.get("資格判定") and transcript:
            # 模型偶爾在續輪漏填判定,沿用上一輪非空的判定,不要記成 None
            for prev in reversed(transcript):
                if prev.get("verdict") and prev["verdict"] != "尚未定案":
                    data["資格判定"] = prev["verdict"]
                    break
        if data is None:
            return {"error": "unparsable", "turn": turn, "raw": raw[:1500],
                    "transcript": transcript}
        questions = [str(q) for q in (data.get("要問學生的問題") or [])]
        # 一字不差(或去掉標點後相同)重複問過的問題 —— 這是停止條件失效的訊號,
        # 量出來而不是幫它擋掉,否則就把要研究的現象藏起來了。
        norm = lambda t: re.sub(r"[\s,。、?!:;「」()（）]", "", t)
        seen = {norm(q) for q in asked_all}
        repeated += sum(1 for q in questions if norm(q) in seen)
        asked_all += questions
        state = str(data.get("狀態") or "")
        transcript.append({"turn": turn, "state": state, "questions": questions,
                           "verdict": data.get("資格判定")})
        # 狀態用詞會飄(實測出現過「需進一步確認」「再問」),所以用「有沒有提出問題」
        # 當主要判準,狀態只作為輔助。沒問題可問就是終局。
        if not questions or "定案" in state or "足夠" in state:
            break
        replies = []
        for q in questions:
            text, key = student_reply(q, profile, student_patterns)
            if key is None:
                unmatched += 1
            replies.append(f"問:{q}\n答:{text}")
        transcript[-1]["replies"] = replies
        prompt = NEXT_TURN.format(answers="\n".join(replies),
                                  verdict_options=cfg["verdict_options"])
    # 迴圈結束時若仍是「尚未定案」,補一次強制定案。
    # 真實系統不可能永遠不回答(agent.py 也是撞到步數上限就強制作答),
    # 而「需要被強制幾次」本身就是停止條件好壞的指標,所以要記下來。
    forced = False
    if not str(data.get("資格判定") or "").strip() or "尚未" in str(data.get("資格判定")):
        forced = True
        opts = cfg["verdict_options"]
        if force_choice:
            # 把「不確定」從選項拿掉。2026-10-20 實測:允許「不確定」時,
            # qwen2.5:14b 在 separate 提示法下 26 題全部選它,等於沒有定案。
            opts = " / ".join(o for o in opts.split(" / ") if "不確定" not in o)
            opts += "(必須從以上選項擇一,不得回答不確定或尚未定案)"
        raw = ask(host, model, FORCE_TURN.format(verdict_options=opts), timeout=timeout)
        forced_data = parse(raw)
        if forced_data:
            data = forced_data
            transcript.append({"turn": len(transcript) + 1, "state": "強制定案",
                               "questions": [], "verdict": data.get("資格判定")})
    return {"data": data, "transcript": transcript, "asked_all": asked_all,
            "turns": len(transcript), "unmatched": unmatched,
            "repeated": repeated, "forced": forced}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--form", default="英語檢測獎勵", choices=sorted(FORMS))
    parser.add_argument("--models", nargs="+", default=["qwen2.5:14b"])
    parser.add_argument("--variant", default="base", choices=sorted(_VARIANT_INSTRUCTION))
    parser.add_argument("--host", default="http://localhost:11434")
    parser.add_argument("--force-choice", action="store_true",
                        help="強制定案時不給「不確定」這個選項,逼模型二選一。"
                             "用來分辨「它真的判斷不出來」與「它只是不肯下結論」")
    parser.add_argument("--max-turns", type=int, default=4,
                        help="最多幾輪。撞到上限代表 Agent 不知道何時該停(對應 RQ3)")
    parser.add_argument("--cases", nargs="*")
    parser.add_argument("--out", default=None)
    parser.add_argument("--fresh", action="store_true")
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()

    cfg = FORMS[args.form]
    documents = load_documents(cfg)
    cases = json.load(io.open(os.path.join(FORMS_DIR, cfg["cases"]),
                              encoding="utf-8"))["cases"]
    if args.cases:
        cases = [c for c in cases if c["id"] in args.cases]

    runs_dir = os.path.join(HERE, "runs")
    os.makedirs(runs_dir, exist_ok=True)
    out_path = args.out or os.path.join(
        runs_dir, f"multiturn_{args.form}_{time.strftime('%Y%m%d_%H%M%S')}.jsonl")

    done = set()
    if os.path.exists(out_path) and not args.fresh:
        for line in io.open(out_path, encoding="utf-8"):
            if line.strip():
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not r.get("error"):
                    done.add((r.get("variant"), r.get("model"), r.get("id")))
        if done:
            print(f"續跑:已有 {len(done)} 題完成,將跳過\n")
    sink = io.open(out_path, "a" if done else "w", encoding="utf-8")

    for model in args.models:
        print("=" * 76)
        print(f"表單:{args.form}  提示法:{args.variant}  模型:{model}  "
              f"最多 {args.max_turns} 輪")
        print("=" * 76)
        stats = {"n": 0, "ok": 0, "turns": 0, "asked": 0, "dup": 0,
                 "unmatched": 0, "hit_cap": 0, "miss": 0, "need": 0, "repeated": 0, "forced": 0}
        for case in cases:
            if (args.variant, model, case["id"]) in done:
                continue
            started = time.time()
            try:
                res = run_case(args.host, model, cfg, documents, case,
                               args.variant, args.max_turns, args.timeout,
                               force_choice=args.force_choice)
            except (urllib.error.URLError, urllib.error.HTTPError,
                    TimeoutError, OSError) as exc:
                print(f"  [{case['id']}] 連線失敗,待補:{exc}")
                continue
            elapsed = time.time() - started
            if res.get("error"):
                print(f"  [{case['id']}] 第 {res['turn']} 輪輸出不是合法 JSON")
                sink.write(json.dumps({"form": args.form, "variant": args.variant,
                                       "model": model, "id": case["id"],
                                       **res}, ensure_ascii=False) + "\n")
                sink.flush()
                continue

            data = res["data"]
            ok = cfg["verdict_ok"](data.get("資格判定"), case)
            got_ask = matched_ids(res["asked_all"], case["must_ask"], cfg["ask"])
            missed = [p for p in case["must_ask"] if p not in got_ask]
            dup = redundant_hits(res["asked_all"], case["must_not_ask"], cfg["redundant"])
            capped = res["turns"] >= args.max_turns and "可以定案" not in str(
                res["transcript"][-1]["state"])

            stats["n"] += 1; stats["ok"] += int(ok); stats["turns"] += res["turns"]
            stats["asked"] += len(res["asked_all"]); stats["dup"] += len(dup)
            stats["unmatched"] += res["unmatched"]; stats["hit_cap"] += int(capped)
            stats["repeated"] += res["repeated"]; stats["forced"] += int(res["forced"])
            stats["miss"] += len(missed); stats["need"] += len(case["must_ask"])

            flags = []
            if missed:   flags.append(f"漏問 {','.join(missed)}")
            if dup:      flags.append(f"冗餘 {','.join(dup)}")
            if res["unmatched"]: flags.append(f"聽不懂 {res['unmatched']}")
            if res["repeated"]:  flags.append(f"重複問 {res['repeated']}")
            if capped:   flags.append("撞上限")
            if res["forced"]: flags.append("被迫定案")
            print(f"  [{case['id']}] {'O' if ok else 'X'} {str(data.get('資格判定'))[:6]:<6}"
                  f" {res['turns']}輪 問{len(res['asked_all'])}句"
                  f"  {'  '.join(flags) if flags else '全中'}  ({elapsed:.0f}s)")

            sink.write(json.dumps({
                "form": args.form, "variant": args.variant,
                "force_choice": args.force_choice, "model": model,
                "id": case["id"], "opening": case["opening"],
                "verdict_got": data.get("資格判定"), "verdict_ok": ok,
                "理由": data.get("理由"), "turns": res["turns"],
                "asked_all": res["asked_all"], "must_ask": case["must_ask"],
                "missed_ask": missed, "redundant": dup,
                "unmatched": res["unmatched"], "repeated": res["repeated"],
                "forced": res["forced"], "hit_cap": capped,
                "transcript": res["transcript"], "elapsed": round(elapsed, 1),
            }, ensure_ascii=False) + "\n")
            sink.flush()

        s = stats
        if s["n"]:
            print(f"\n  {model}: {s['n']} 題  資格判定 {s['ok']}/{s['n']}={s['ok']/s['n']:.1%}"
                  f"  平均 {s['turns']/s['n']:.1f} 輪、問 {s['asked']/s['n']:.1f} 句"
                  f"  漏問 {s['miss']}/{s['need']}={s['miss']/max(s['need'],1):.1%}"
                  f"  冗餘 {s['dup']}  重複問 {s['repeated']}  聽不懂 {s['unmatched']}"
                  f"  撞上限 {s['hit_cap']}  被迫定案 {s['forced']}")
        print()
    sink.close()
    print(f"逐題紀錄:{out_path}")
    print("注意:模擬學生以正規表示式查表,「聽不懂」的次數是評測失真的指標,數字大就要補規則。")


if __name__ == "__main__":
    main()
