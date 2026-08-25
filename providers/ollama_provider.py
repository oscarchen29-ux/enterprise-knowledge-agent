import json

from openai import OpenAI

from providers.base import LLMProvider


class OllamaProvider(LLMProvider):
    def __init__(self, model: str = "qwen2.5:7b", base_url: str = "http://localhost:11434/v1"):
        self.model = model
        self.client = OpenAI(base_url=base_url, api_key="ollama")

    def generate(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=tools,
        )
        message = response.choices[0].message

        tool_calls = []
        for call in message.tool_calls or []:
            tool_calls.append({
                "id": call.id,
                "name": call.function.name,
                "arguments": json.loads(call.function.arguments),
            })

        return {"content": message.content, "tool_calls": tool_calls}
