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


def retrieve_documents(opening):
    """retrieval 模式:不指定文件,用學生的開場白從全部 110 份 821 段裡檢索。

    為什麼要有這個對照:
    full 模式等於**事先把正確的那一份法規交給模型**,但真實使用時沒有人會先挑好 ——
    使用者只說「我多益考了870分」,系統得自己在 110 份法規裡找到
    《學生參與英語能力檢測獎勵要點》。兩者的差距就是「文件選擇」這一步的代價。

    tau-bench(arXiv:2406.12045)把整份 domain policy 直接放進 system prompt,
    所以這一步在它的設定裡**不存在**。這是本專案與它最主要的結構差異。

    注意:單一法規其實很短(英語 2358 字、宿舍 1008 字,都遠小於 16384 的 context),
    所以兩組的差距**不是來自 context 裝不下**,而是來自在多份文件中找對那一份。
    """
    if ROOT not in sys.path:          # tools.py 在專案根目錄,不在 benchmark/
        sys.path.insert(0, ROOT)
    import tools                      # 延後匯入:full 模式不需要載入 bge-m3
    return "【檢索到的法規段落】\n" + tools.search_documents(opening) + "\n"


def retrieve_multi_aspect(opening, cfg):
    """aspect 模式:不只用使用者的原話查一次,而是針對每個「前置條件類型」各查一次。

    2026-10-20 的診斷:檢索撈不到「與使用者問句語意無關、但必須告知」的條文。
    實測單一查詢的命中情形(英語檢測獎勵,目標是寫著申請期限的第 4 段):

        使用者原話「我多益考了870分,可以申請獎勵嗎?」  -> 撈到第 1,2,3 段,**沒有第 4 段**
        任務式「英語能力檢測獎勵的申請規定、期限與應檢附文件」-> 撈到第 1,2,**4** 段
        只問期限「英語檢測獎勵申請期限 截止 逾時」       -> 撈到第 1,3,**4** 段
        原話 + 任務黏成一句                              -> 撈回第 1,2,3 段,**又漏了**

    最後一行是關鍵:**把多個面向黏成一個長查詢沒有用,會互相稀釋,必須分開查。**

    面向不是任意挑的,就是 forms/*.json 裡 preconditions 的「類型」欄位
    (成績門檻 / 排除條件 / 期限 / 應檢附文件 / 受理單位)。也就是說,
    人工標註時已經寫出了「一項行政程序有哪幾種前置條件」,這裡只是把它
    當成查詢的面向清單。

    這是**固定面向**版本,查什麼由人寫死,換一個領域就要重寫 —— 它的用途是
    當上界,回答「這條路最多能補回多少」。讓模型自己決定要查哪些面向
    才是 RQ1 要研究的問題。
    """
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    import tools

    chunks = tools._load_chunks()
    seen, blocks = set(), []
    for label, query in [("使用者的問題", opening)] + cfg["aspects"]:
        ranked = tools._m3_ranking(query) or tools._rrf_ranking(query)
        if not ranked:
            continue
        ranked = tools._diversify(ranked)
        picked = []
        for pos in ranked[:cfg.get("aspect_top_k", 3)]:
            if pos in seen:               # 不同面向撈到同一段就不重複貼
                continue
            seen.add(pos)
            c = chunks[pos]
            picked.append(f"[{c['file']} 第{c['index'] + 1}段]\n{c['text']}")
        if picked:
            blocks.append(f"〈查詢:{label}〉\n" + "\n\n".join(picked))
    return "【檢索到的法規段落】\n" + "\n\n".join(blocks) + "\n" if blocks else "找不到相關文件。"


ASPECT_PLAN_PROMPT = """你是學校的行政助理,要幫學生辦一項申請。
你還沒有看到任何法規內容,只知道學校有下面這些法規文件:

{titles}

【學生說】
{opening}

在查資料之前,先想清楚:**要判斷這件事並且告訴學生該準備什麼,你需要查哪些東西?**

請輸出 3 到 6 個**搜尋查詢**,每個查詢針對一個不同的面向。
查詢要寫成「法規裡會出現的字詞」,不是「學生會怎麼問」。
例如要找截止日,寫「申請期限 截止 逾時」會比寫「什麼時候要交」有效。

只輸出 JSON,不要有其他文字:
{{"查詢": ["...", "...", "..."]}}"""

# 2026-10-26 實測:qwen3.8:27b 自行生成的查詢有 61% 含簡體字,100% 的題目至少出現一次
# (gemma4:12b 為 17% / 65%)。知識庫是繁體中文,檢索為字面比對,
# 「奖励」命中不了「獎勵」,因此這些查詢等於白查。下面這段是修法,做成開關以便對照。
TRAD_HINT = """

**所有查詢必須使用繁體中文(台灣用字)。** 知識庫是繁體中文的法規,
檢索是逐字比對,簡體字查不到任何東西 —— 例如「奖励」找不到「獎勵」、
「检测」找不到「檢測」。輸出前請逐字檢查。"""


def retrieve_self_aspect(opening, cfg, host, model, think, timeout=900, trad_hint=False):
    """self-aspect 模式:讓模型自己決定要查哪些面向,再用它給的查詢去檢索。

    與 aspect 模式的差別就是**誰決定面向**:
      aspect       面向由人寫死在 forms_config.py,換領域要重寫
      self-aspect  模型先看文件標題清單(不給全文),自己列出要查什麼

    這是 RQ1 的核心:缺資訊時,Agent 知不知道自己該去查什麼?
    aspect 是人工上界,self-aspect 量的是模型自己能走到哪裡。

    回傳 (提示用的文字, 模型實際生成的查詢清單)。查詢清單要記錄下來 ——
    它比分數更有價值,因為看得出模型想到了哪些面向、漏了哪些。
    """
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    import tools

    chunks = tools._load_chunks()
    titles = sorted({c["file"] for c in chunks})
    plan_prompt = ASPECT_PLAN_PROMPT.format(
        titles="\n".join("  - " + t for t in titles), opening=opening)
    if trad_hint:
        plan_prompt += TRAD_HINT
    try:
        plan = parse(ask(host, model, plan_prompt, timeout=timeout, think=think))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        raise
    queries = [str(q) for q in (plan or {}).get("查詢", []) if str(q).strip()]
    if not queries:
        # 模型沒給出查詢:退回只用使用者原話,並把這件事記錄下來
        queries = [opening]

    seen, blocks = set(), []
    for q in queries[:6]:
        ranked = tools._m3_ranking(q) or tools._rrf_ranking(q)
        if not ranked:
            continue
        ranked = tools._diversify(ranked)
        picked = []
        for pos in ranked[:cfg.get("aspect_top_k", 3)]:
            if pos in seen:
                continue
            seen.add(pos)
            c = chunks[pos]
            picked.append(f"[{c['file']} 第{c['index'] + 1}段]\n{c['text']}")
        if picked:
            blocks.append(f"〈查詢:{q}〉\n" + "\n\n".join(picked))
    text = ("【檢索到的法規段落】\n" + "\n\n".join(blocks) + "\n") if blocks else "找不到相關文件。"
    return text, queries


def retrieve_whole_documents(opening, max_docs=1):
    """doc 模式:先用檢索決定是「哪幾份」法規,再把那幾份整份放進提示。

    為什麼需要這個中間模式:
    2026-10-20 的 full vs retrieval 對照顯示,檢索模式的告知完整率掉 17 到 22 個
    百分點,而且惡化集中在少數幾項 —— 英語的 P4(申請期限)從漏 0 次變成漏 4 次、
    宿舍的 D7(保障生依級別分配)從漏 1 次變成漏 6 次。共同點是這些條文與使用者
    的問題語意無關:學生問「我多益870能申請嗎」,檢索撈回成績門檻那幾段,
    但期限寫在第四點,撈不到。

    也就是說,檢索的查詢對齊的是「使用者問什麼」,任務需要的卻是
    「法規還規定了什麼」,兩者系統性不一致。

    doc 模式把問題從「選對段落」降級成「選對文件」:段落只用來投票決定文件,
    決定之後整份讀入,所以同一份法規裡與問題語意無關的條文也會進到提示。
    單一法規很短(英語 2358 字、宿舍 1008 字),整份放得進 16384 的 context。
    選錯文件仍然會錯,所以這不是把檢索問題消掉,只是換一個層級。
    """
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    import tools

    chunks = tools._load_chunks()
    ranked = tools._m3_ranking(opening)
    if ranked is None:
        ranked = tools._rrf_ranking(opening)
    if not ranked:
        return "找不到相關文件。"
    ranked = tools._diversify(ranked)

    # 把前 TOP_K 段涉及的所有文件整份讀入,依最佳名次排序,不做投票。
    #
    # 一開始用的是投票(每段替所屬檔案投一票、取票數最高的兩份),實測只有 46% / 21%
    # 選到正確法規。診斷後發現檢索其實找得到——正確法規出現在前 6 段的比率是
    # 85% / 89%,只是排名第 1 的只有 23% / 21%,首次出現的名次中位數是第 3 名。
    # 加上 tools.DISTINCT_FILES = 4 的多樣性機制強制前 4 名來自不同檔案,
    # 每份文件最多只拿得到 1 到 2 票,投票在這個設計下本來就失效。
    # 改成全部收下之後,文件覆蓋率等於「正確法規出現在前 TOP_K 段」的比率。
    seen, picked = set(), []
    for pos in ranked[:tools.TOP_K]:
        name = chunks[pos]["file"]
        if name not in seen:
            seen.add(name)
            picked.append(name)
    picked = picked[:max_docs]

    # 長度與覆蓋率的取捨(2026-10-20 實測,num_ctx=16384):
    #   max_docs=2  覆蓋 50%/32%,平均 6.4k/9.2k 字
    #   max_docs=4  覆蓋 65%/79%,平均 14.7k/20.9k 字  <- 宿舍 16/19 題超出上限
    #   max_docs=6  覆蓋 85%/89%,平均 18.6k/25.1k 字  <- 兩張都超出
    # 要高覆蓋率就一定超過 context。所以預設採混合式:**排名最高的那一份整份讀入,
    # 其餘仍以段落呈現**,長度可控而且保證至少有一份完整法規(期限、應檢附文件
    # 這類與問題語意無關的條文才有機會進到提示)。max_docs 加大則退回純整份模式。
    full_names = picked[:max_docs]          # 要整份讀入的文件,其餘以段落補
    parts = []
    for name in full_names:
        path = os.path.join(DOCS, name)
        if not os.path.exists(path):
            continue
        text = io.open(path, encoding="utf-8").read()
        body = text.split("【條文】", 1)[1].strip() if "【條文】" in text else text.strip()
        parts.append("【完整法規:" + name + "】\n" + body + "\n")
    rest = [p for p in ranked[:tools.TOP_K] if chunks[p]["file"] not in full_names]
    if rest:
        seg = [f"[{chunks[p]['file']} 第{chunks[p]['index'] + 1}段]\n{chunks[p]['text']}"
               for p in rest]
        parts.append("【其他相關段落】\n" + "\n\n".join(seg) + "\n")
    return "\n".join(parts) if parts else "找不到相關文件。"


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
    parser.add_argument("--context", choices=["full", "retrieval", "doc", "aspect", "self-aspect"], default="full",
                        help="full=整份對應法規塞進提示(等同 tau-bench 的設定);"
                             "retrieval=不指定文件,只給檢索回來的段落;"
                             "doc=先用檢索決定是哪幾份,再整份讀入;"
                             "aspect=每個前置條件類型各查一次(人工固定面向);"
                             "self-aspect=模型先看文件標題清單,自己決定查什麼")
    parser.add_argument("--trad-hint", action="store_true",
                        help="self-aspect 模式:在提示中明確要求查詢必須用繁體中文。"
                             "用來對照「模型自行生成簡體查詢」這個失敗模式修掉後的差異")
    parser.add_argument("--think", choices=["on", "off"], default=None,
                        help="思考型模型是否開啟思考。不指定就用模型預設")
    parser.add_argument("--fresh", action="store_true",
                        help="不續跑,整批重來(預設會跳過 out 檔裡已完成的題目)")
    parser.add_argument("--repeats", type=int, default=1,
                        help="同一題跑幾次。跑一次的數字是噪音,要談差異至少跑三次")
    args = parser.parse_args()

    cfg = FORMS[args.form]
    documents = load_documents(cfg) if args.context == "full" else None
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
                done.add((rec.get("context", "full"), rec.get("trad_hint", False), rec.get("variant", "base"),
                          rec.get("model"), rec.get("id"), rec.get("run", 1)))
        if done:
            print(f"續跑:{out_path} 已有 {len(done)} 筆完成的紀錄,將跳過")
    sink = io.open(out_path, "a" if done else "w", encoding="utf-8")

    summary = []
    for model in args.models:
        print("=" * 72)
        print(f"表單:{args.form}  提示法:{args.variant}  文件:{args.context}  模型:{model}  題數:{len(cases)} x {args.repeats} 次")
        print("=" * 72)
        ask_need = ask_hit = tell_need = tell_hit = 0
        redundant = verdict_right = parsed = 0

        self_queries = None
        pending = [(c, r) for r in range(1, args.repeats + 1) for c in cases
                   if (args.context, args.trad_hint, args.variant, model, c["id"], r) not in done]
        if not pending:
            print("  這個模型的題目都已完成,跳過")
        for case, run_no in pending:
            started = time.time()
            tag = f"{case['id']}" + (f"#{run_no}" if args.repeats > 1 else "")
            try:
                # retrieval 模式每題各自檢索,所以不能在迴圈外先算好
                if documents is not None:
                    docs_for_case = documents
                elif args.context == "doc":
                    docs_for_case = retrieve_whole_documents(case["opening"])
                elif args.context == "aspect":
                    docs_for_case = retrieve_multi_aspect(case["opening"], cfg)
                elif args.context == "self-aspect":
                    docs_for_case, self_queries = retrieve_self_aspect(
                        case["opening"], cfg, args.host, model,
                        None if args.think is None else (args.think == "on"),
                        timeout=args.timeout if hasattr(args, "timeout") else 900,
                        trad_hint=args.trad_hint)
                else:
                    docs_for_case = retrieve_documents(case["opening"])
                raw = ask(args.host, model,
                          build_prompt(args.variant, docs_for_case,
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
                "form": args.form, "variant": args.variant, "context": args.context,
                "trad_hint": args.trad_hint, "self_queries": self_queries, "model": model,
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
            "model": f"{model} [{args.variant}/{args.context}"
                     + ("+繁" if args.trad_hint else "") + "]",
            "題數": len(cases) * args.repeats, "可解析": parsed,
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
