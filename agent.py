import argparse
import json
import re
import sys

from opencc import OpenCC

# Windows 主控台預設 cp950,印不出簡體字。模型很常吐簡體(這正是要用 OpenCC 的原因),
# 而 log 裡會直接印出模型產生的查詢字串 —— 只要它送出「病假证明」這種查詢,
# 光是印出來就會 UnicodeEncodeError,整個請求當掉。輸出一律轉 UTF-8。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from providers.ollama_provider import OllamaProvider
from tools import ASK_MARKER, TOOLS_SCHEMA, TOOL_FUNCTIONS

# 模型(尤其是中國訓練的開源模型)常會吐出簡體字，就算 prompt 明確要求繁體也未必有效，
# 用 OpenCC 做後處理轉換比較穩定。用 s2tw(字形+基本詞彙)而非 s2twp，
# 因為 s2twp 的積極詞彙替換會誤轉一詞多義的字(例如「程序」誤轉成「程式」)。
_s2tw = OpenCC("s2tw")

SYSTEM_PROMPT = (
    "你是學校系所行政助理。使用者(學生/教師/系辦人員)會問跟系所規定相關的多步驟問題"
    "(例如選課、畢業門檻、獎學金申請、論文格式),你可以呼叫 search_documents 工具查詢"
    "系所規定文件,查到資料後再根據文件內容回答,不要憑空編造規定內容。"
    "務必全程使用繁體中文回答,不要出現簡體字。"
    "\n\n"
    # 很多規定依入學學年度或學制而不同,同一個問題對不同的人有不同的正確答案。
    # 沒有這段指示時,模型會把各屆的數字合併成「15 至 16 學分」這種範圍 ——
    # 對任何一個具體的學生來說都是錯的。寧可追問,不要給範圍。
    "重要:很多規定會因為入學學年度或學制而不同。如果缺少判斷所需的條件,"
    "請呼叫 ask_clarification 工具向使用者追問,不要自行猜測、也不要給出「15 至 16 學分」"
    "這種涵蓋多屆的範圍。必須追問的情況包括:問到學分或畢業條件卻沒說入學學年度;"
    "問到抵免或獎勵卻沒說學制;說「轉進來」卻沒說是轉系生還是轉學生。"
    "\n"
    "但如果檢索到的文件對所有屆別都一致,就直接回答,不需要追問。"
)

VERIFY_PROMPT = (
    "你是事實查核員。以下是查到的原始文件內容，以及助理草擬的回答。"
    "請逐項檢查回答中的每一項主張是否有文件依據：\n"
    "- 若某個主張文件中找不到根據，請刪除或改成「文件未提及」，不要保留編造內容。\n"
    "- 若文件確實支持，保留原意即可。\n"
    "- 若草稿中出現簡體字，一併改寫成繁體中文。\n"
    "只要輸出修正後的最終回答文字，全部使用繁體中文，不要加說明或前言。"
)

MAX_STEPS = 5

# 模型有時不走 OpenAI 相容 API 的 tool_calls 欄位,而是把呼叫請求當成一般文字吐在
# content 裡(常伴隨無意義的 token,例如「 Closet」「portun」)。Ollama 的相容層不會
# 幫忙解析,結果 result["tool_calls"] 是空的,這一輪就被誤判成「模型不需要查文件」,
# 直接拿模型記憶作答 —— 而且因為 retrieved_context 是空的,自我驗證也一併被跳過。
# 31 題 x 3 次的基線測試裡,93 次執行有 35 次(38%)是這樣壞掉的,全程不報錯。
_LEAKED_CALL_RE = re.compile(r'\{\s*"name"\s*:\s*"(?P<name>[\w.\-]+)"')


def _recover_leaked_tool_calls(content: str) -> list[dict]:
    """把寫進一般文字裡的工具呼叫解析回來,格式與 provider 回傳的 tool_calls 相同。

    模型其實判斷對了(知道該查文件、也想好了查詢字串),只是寫錯欄位,沒必要浪費。
    """
    if not content:
        return []

    decoder = json.JSONDecoder()
    recovered = []
    for match in _LEAKED_CALL_RE.finditer(content):
        try:
            payload, _ = decoder.raw_decode(content, match.start())
        except ValueError:
            # 模型可能把括號寫壞或被截斷(觀察到「..."query": "退學 學分"}大大小」),
            # 這種情況退而求其次,只把 query 字串撈出來。
            query = re.search(r'"query"\s*:\s*"(.*?)"', content[match.start():])
            if not query:
                continue
            payload = {"name": match.group("name"), "arguments": {"query": query.group(1)}}

        arguments = payload.get("arguments")
        if isinstance(arguments, str):  # 有時 arguments 自己又被包成 JSON 字串
            try:
                arguments = json.loads(arguments)
            except ValueError:
                continue
        if not isinstance(arguments, dict):
            continue

        recovered.append({
            "id": f"recovered_{len(recovered)}",
            "name": payload.get("name") or match.group("name"),
            "arguments": arguments,
        })
    return recovered


def verify_answer(draft: str, retrieved_context: list[str], provider) -> str:
    if not retrieved_context:
        return draft

    messages = [
        {"role": "system", "content": VERIFY_PROMPT},
        {"role": "user", "content": f"【原始文件內容】\n{chr(10).join(retrieved_context)}\n\n【草擬回答】\n{draft}"},
    ]
    result = provider.generate(messages)
    return result["content"] or draft


# 只有「答案真的會因為條件不同而不同」的題目才追問,否則每次都反問很煩人。
# 這裡的兩條規則都對應到實測過的錯誤:
#
# 1) 共同課程/通識學分:111、112 級是 15/16,113 級起是 16/15。實測問「畢業要修
#    幾學分」時模型檢索到五屆的表,回答「共同課程 15 至 16 學分」—— 對任何一個
#    具體的學生都是錯的。(總學分 128 與系必修 49 各屆相同,所以不觸發追問。)
#
# 2) 抵免:轉系生與轉學生適用《學生抵免學分辦法》第三條的不同款,轉學生不受
#    轉系生的上限限制。實測模型會把轉系生的規定套到轉學生身上。
#
# 模型自己也有 ask_clarification 工具可用,但 7B 判斷「我資訊夠不夠」很不可靠,
# 實測兩題都沒觸發,所以再加一層確定性的檢查。
_COHORT_HINT = re.compile(r"1\d{2}\s*(級|學年)|入學")
_CLARIFY_RULES = [
    (re.compile(r"(共同課程|通識).{0,10}(學分|幾學分)|學分.{0,10}(共同課程|通識)"),
     _COHORT_HINT,
     "共同課程與通識的學分數在 113 學年度調整過(111、112 級是共同 15、通識 16;"
     "113 級起是共同 16、通識 15)。請問你是哪一學年度入學的?"),
    (re.compile(r"抵免"),
     re.compile(r"轉系|轉學|重考|在職|碩士|博士|研究所"),
     "抵免規定會因為身分而不同,轉系生與轉學生適用的條款不一樣。"
     "請問你是轉系生、轉學生,還是其他情況(例如重考入學、碩博班)?"),
]


def needs_clarification(task: str) -> str | None:
    """條件不足時回傳要追問的問題,否則回傳 None。"""
    for topic, disambiguator, question in _CLARIFY_RULES:
        if topic.search(task) and not disambiguator.search(task):
            return question
    return None


def run_task(task: str, provider) -> str:
    question = needs_clarification(task)
    if question:
        print("[step -] 問題缺少關鍵條件,先向使用者追問")
        return question

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task},
    ]
    retrieved_context = []

    for step in range(MAX_STEPS):
        result = provider.generate(messages, tools=TOOLS_SCHEMA)
        tool_calls = result["tool_calls"]
        content = result["content"] or ""

        if not tool_calls:
            tool_calls = _recover_leaked_tool_calls(content)
            if tool_calls:
                print(f"[step {step}] 從文字中救回 {len(tool_calls)} 個外洩的工具呼叫")
                # 殘骸(亂碼 token + JSON 字串)不要留在對話紀錄裡污染後續生成
                content = ""

        if not tool_calls:
            # 模型既沒查文件、也沒產生任何內容。原本會回傳空字串,使用者看到一片空白,
            # 可能誤以為「系統查不到就是沒這條規定」,比明講失敗更危險。
            if not content.strip():
                print(f"[step {step}] 模型回傳空白內容且未呼叫工具")
                return "模型未產生有效回答,也沒有查詢任何文件。請重新提問,或把問題描述得更具體。"

            # 保底檢索:模型有時直接憑記憶作答,一次工具都不呼叫,而且答得像模像樣
            # (實測它會編出《學生手冊》裡不存在的章節編號)。這種答案沒有任何文件
            # 依據,而且因為 retrieved_context 是空的,自我驗證也會被跳過 ——
            # 兩道防線同時失效。所以在真的要回答之前,至少強制查一次。
            #
            # 這是保底而不是每輪強制:模型自己決定查什麼仍然是主要路徑,只有它
            # 完全沒查時才介入,agent 的規劃行為得以保留。
            if not retrieved_context:
                print(f"[step {step}] 未經查詢即作答,強制檢索一次後重新回答")
                fallback = TOOL_FUNCTIONS["search_documents"](query=task)
                retrieved_context.append(fallback)
                messages.append({
                    "role": "user",
                    "content": f"以下是系統查到的相關文件:\n\n{fallback}\n\n"
                               f"請根據上述文件重新回答原本的問題。文件沒有提到的內容,"
                               f"請明確說「文件未提及」,不要自行補充。",
                })
                continue
            print(f"[step {step}] 草擬回答完成，進入自我驗證")
            verified = verify_answer(content, retrieved_context, provider)
            return _s2tw.convert(verified)

        messages.append({
            "role": "assistant",
            "content": content,
            "tool_calls": [
                {
                    "id": call["id"],
                    "type": "function",
                    "function": {"name": call["name"], "arguments": json.dumps(call["arguments"], ensure_ascii=False)},
                }
                for call in tool_calls
            ],
        })

        for call in tool_calls:
            print(f"[step {step}] 呼叫工具 {call['name']}({call['arguments']})")
            fn = TOOL_FUNCTIONS.get(call["name"])
            try:
                if fn is None:
                    raise ValueError(f"未知工具: {call['name']}")
                tool_result = fn(**call["arguments"])
            except TypeError as e:
                print(f"[step {step}] 工具參數格式錯誤: {e}")
                tool_result = f"工具呼叫失敗,參數格式不正確({e})。請改用正確格式重新呼叫 search_documents(query=...)。"
            else:
                # 模型判斷條件不足、要向使用者追問。直接把問題交還給使用者,
                # 不要繼續跑迴圈 —— 缺的資訊只有使用者能補。
                if isinstance(tool_result, str) and tool_result.startswith(ASK_MARKER):
                    question = tool_result[len(ASK_MARKER):].strip()
                    print(f"[step {step}] 條件不足,向使用者追問")
                    return _s2tw.convert(question)
                retrieved_context.append(tool_result)
            messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": tool_result,
            })

    return "已達最大步驟數,任務未完成。"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="qwen2.5:7b")
    parser.add_argument(
        "--task",
        default="我是轉系生,轉入資工系三年級,最多可以抵免多少學分?畢業總共需要修滿多少學分?",
    )
    args = parser.parse_args()

    provider = OllamaProvider(model=args.model)
    answer = run_task(args.task, provider)
    print("\n=== 最終回答 ===")
    print(answer)
