import json
import time
import urllib.error
import urllib.request

from providers.base import LLMProvider

# 連線中斷時重試幾次、每次間隔幾秒(第 n 次等 RETRY_WAIT_SEC × n 秒)。
RETRY_LIMIT = 4
RETRY_WAIT_SEC = 5

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


def _seconds(nanoseconds):
    return None if nanoseconds is None else round(nanoseconds / 1e9, 3)


class OllamaProvider(LLMProvider):
    def __init__(self, model: str = "qwen2.5:7b",
                 base_url: str = "http://localhost:11434",
                 num_ctx: int = DEFAULT_NUM_CTX,
                 think: bool | None = None):
        self.model = model
        self.base_url = base_url.rstrip("/").removesuffix("/v1")
        self.num_ctx = num_ctx
        # 思考型模型(例如 gemma4:31b)預設會先產生一段推理再回答。這段推理這裡從來
        # 不讀,時間卻照花:實測 agent 第一步,思考開著 29.4 秒、產生 176 個 token,
        # think=False 只要 6.7 秒、25 個 token(各跑一次)。
        # None 表示不送這個欄位、維持 Ollama 預設行為。不支援思考的模型(qwen2.5:7b)
        # 收到 False 不會報錯。關掉思考會不會讓答案變差還沒評估過,所以預設不改。
        self.think = think

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

    def _post(self, payload: dict) -> dict:
        """送出請求;連線類的錯誤重試,其他錯誤直接拋出。

        模型跑在另一台機器(Jetson)時,連線會經過 SSH 通道。通道短暫中斷會讓
        urlopen 立刻拋 ConnectionResetError / URLError,而 benchmark 把整題記成崩潰 ——
        2026-09-23 的第一輪就因此掉了最後三題,第二輪 32 題全滅。重試讓短暫斷線
        只損失幾秒,不會毀掉整批結果。

        不重試 HTTP 錯誤(HTTPError):那是請求本身有問題(例如格式錯、模型不存在),
        重送幾次也一樣,只會拖時間。
        """
        request = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        for attempt in range(RETRY_LIMIT):
            try:
                with urllib.request.urlopen(request, timeout=600) as response:
                    return json.load(response)
            except urllib.error.HTTPError:
                raise
            except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
                if attempt == RETRY_LIMIT - 1:
                    raise
                wait = RETRY_WAIT_SEC * (attempt + 1)
                print(f"[模型] 連線失敗({type(exc).__name__}),{wait} 秒後重試 "
                      f"({attempt + 1}/{RETRY_LIMIT - 1})", flush=True)
                time.sleep(wait)
        raise RuntimeError("unreachable")

    def generate(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        payload = {
            "model": self.model,
            "messages": self._to_native(messages),
            "stream": False,
            "options": {"num_ctx": self.num_ctx},
        }
        if tools:
            payload["tools"] = tools
        if self.think is not None:
            payload["think"] = self.think

        result = self._post(payload)

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
            # 一次請求分兩段計時:讀輸入(prompt_eval_*)與逐 token 生成(eval_*)。
            # 讀輸入主要吃運算能力;生成每個 token 都要把整份權重讀一遍,主要吃記憶體頻寬。
            # 要判斷換機器或改程式哪個有用,得先知道時間花在哪一段。
            # Ollama 回傳的單位是奈秒;思考開著時 eval_count 也包含思考的 token。
            "prompt_sec": _seconds(result.get("prompt_eval_duration")),
            "output_tokens": result.get("eval_count"),
            "output_sec": _seconds(result.get("eval_duration")),
            "total_sec": _seconds(result.get("total_duration")),
        }
