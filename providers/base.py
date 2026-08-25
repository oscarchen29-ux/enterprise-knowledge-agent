from abc import ABC, abstractmethod


class LLMProvider(ABC):
    """統一介面,讓 agent.py 不用管背後是 Ollama、Claude API 還是 Spark 推論服務。"""

    @abstractmethod
    def generate(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        """
        messages: OpenAI 風格的對話紀錄 [{"role": "user"/"assistant"/"tool", "content": ...}, ...]
        tools: OpenAI 風格的工具定義(JSON Schema),沒有就傳 None

        回傳統一格式:
        {
            "content": str | None,       # 模型的文字回應
            "tool_calls": [              # 模型要呼叫的工具,沒有就是空list
                {"id": str, "name": str, "arguments": dict},
                ...
            ],
        }
        """
        raise NotImplementedError
