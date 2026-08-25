import argparse
import json

from opencc import OpenCC

from providers.ollama_provider import OllamaProvider
from tools import TOOLS_SCHEMA, TOOL_FUNCTIONS

# 模型(尤其是中國訓練的開源模型)常會吐出簡體字，就算 prompt 明確要求繁體也未必有效，
# 用 OpenCC 做後處理轉換比較穩定。用 s2tw(字形+基本詞彙)而非 s2twp，
# 因為 s2twp 的積極詞彙替換會誤轉一詞多義的字(例如「程序」誤轉成「程式」)。
_s2tw = OpenCC("s2tw")

SYSTEM_PROMPT = (
    "你是學校系所行政助理。使用者(學生/教師/系辦人員)會問跟系所規定相關的多步驟問題"
    "(例如選課、畢業門檻、獎學金申請、論文格式),你可以呼叫 search_documents 工具查詢"
    "系所規定文件,查到資料後再根據文件內容回答,不要憑空編造規定內容。"
    "務必全程使用繁體中文回答,不要出現簡體字。"
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


def verify_answer(draft: str, retrieved_context: list[str], provider) -> str:
    if not retrieved_context:
        return draft

    messages = [
        {"role": "system", "content": VERIFY_PROMPT},
        {"role": "user", "content": f"【原始文件內容】\n{chr(10).join(retrieved_context)}\n\n【草擬回答】\n{draft}"},
    ]
    result = provider.generate(messages)
    return result["content"] or draft


def run_task(task: str, provider) -> str:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task},
    ]
    retrieved_context = []

    for step in range(MAX_STEPS):
        result = provider.generate(messages, tools=TOOLS_SCHEMA)

        if not result["tool_calls"]:
            print(f"[step {step}] 草擬回答完成，進入自我驗證")
            verified = verify_answer(result["content"], retrieved_context, provider)
            return _s2tw.convert(verified)

        messages.append({
            "role": "assistant",
            "content": result["content"],
            "tool_calls": [
                {
                    "id": call["id"],
                    "type": "function",
                    "function": {"name": call["name"], "arguments": json.dumps(call["arguments"], ensure_ascii=False)},
                }
                for call in result["tool_calls"]
            ],
        })

        for call in result["tool_calls"]:
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
