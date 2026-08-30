import json
import urllib.request

from providers.base import LLMProvider

# Ollama 的 num_ctx 預設是 2048,而且無論模型本身支援多長都一樣 ——
# qwen2.5:7b 支援 32768,實際卻只吃 2048。檢索一次回傳 4~9k tokens 的情況下,
# 七成以上的文件內容在進模型之前就被無聲截掉,模型看不到卻不會報錯。
#
# 這個值不能透過 OpenAI 相容層設定:即使用 extra_body 傳 options.num_ctx,
# /v1/chat/completions 仍然回報 prompt_tokens=2050。只有原生 /api/chat 會生效,
# 因此這裡改用原生端點(它同樣支援 tools)。
#
# 16384 是實測後的取捨:8192 已足夠容納目前最長的檢索結果,再往上對延遲影響很小
# (32768 只多約 2 秒),但 KV cache 會多吃 VRAM,而這台機器只有 8GB。
DEFAULT_NUM_CTX = 16384


class OllamaProvider(LLMProvider):
    def __init__(self, model: str = "qwen2.5:7b",
                 base_url: str = "http://localhost:11434",
                 num_ctx: int = DEFAULT_NUM_CTX):
        self.model = model
        self.base_url = base_url.rstrip("/").removesuffix("/v1")
        self.num_ctx = num_ctx

    @staticmethod
    def _to_native(messages: list[dict]) -> list[dict]:
        """把 base.py 約定的 OpenAI 風格對話紀錄轉成 Ollama 原生格式。

        翻譯放在 provider 而不是 agent.py:介面說好是 OpenAI 風格,agent 不該
        為了某一家後端改寫自己的資料結構,否則抽象層就失去意義。

        兩者的差異:原生格式的 tool_calls 沒有 type 欄位,arguments 是物件而不是
        JSON 字串;tool 訊息用 tool_name 對應工具,而不是 tool_call_id。
        送錯格式會直接吃到 HTTP 400。
        """
        id_to_name = {}
        native = []
        for message in messages:
            role = message.get("role")

            if role == "assistant" and message.get("tool_calls"):
                calls = []
                for call in message["tool_calls"]:
                    function = call.get("function", call)
                    arguments = function.get("arguments", {})
                    if isinstance(arguments, str):
                        try:
                            arguments = json.loads(arguments)
                        except ValueError:
                            arguments = {}
                    name = function.get("name", "")
                    if call.get("id"):
                        id_to_name[call["id"]] = name
                    calls.append({"function": {"name": name, "arguments": arguments}})
                native.append({
                    "role": "assistant",
                    "content": message.get("content") or "",
                    "tool_calls": calls,
                })

            elif role == "tool":
                entry = {"role": "tool", "content": message.get("content") or ""}
                name = id_to_name.get(message.get("tool_call_id"))
                if name:
                    entry["tool_name"] = name
                native.append(entry)

            else:
                native.append({"role": role, "content": message.get("content") or ""})

        return native

    def generate(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        payload = {
            "model": self.model,
            "messages": self._to_native(messages),
            "stream": False,
            "options": {"num_ctx": self.num_ctx},
        }
        if tools:
            payload["tools"] = tools

        request = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=600) as response:
            result = json.load(response)

        message = result.get("message", {})
        tool_calls = []
        for index, call in enumerate(message.get("tool_calls") or []):
            function = call.get("function", {})
            arguments = function.get("arguments", {})
            # 原生端點回傳的 arguments 已經是 dict,不像 OpenAI 相容層是 JSON 字串;
            # 兩種都接受,免得日後換端點又要改 agent.py。
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except ValueError:
                    arguments = {}
            tool_calls.append({
                "id": call.get("id") or f"call_{index}",
                "name": function.get("name", ""),
                "arguments": arguments,
            })

        return {
            "content": message.get("content"),
            "tool_calls": tool_calls,
            # 讓 benchmark 能記錄實際吃進去的 token 數,避免再次發生
            # 「以為文件送進去了、其實被截斷」這種無聲失敗。
            "prompt_tokens": result.get("prompt_eval_count"),
        }
