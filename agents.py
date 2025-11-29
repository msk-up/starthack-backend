from typing import Any

import json
from pydantic import BaseModel, Field


class Message(BaseModel):
    role: str
    content: str
    timestamp: str | None = None


class NegotiationAgent:
    async def __init__(
        self,
        db_pool: Any,
        client: Any,
        sys_prompt: str,
        insights: str,
        product: str,
    ) -> None:
        self.client = client
        self.db_pool = db_pool
        self.sys_prompt = sys_prompt
        self.insights = insights
        self.product = product
        messages = db_pool.fetch("SELECT * FROM messages WHERE ng_id = $1 AND supplier_id  =$ 2", product, insights)

    def _build_conversation(self) -> list[dict[str, str]]:
        conversation: list[dict[str, str]] = []
        if self.sys_prompt:
            conversation.append({"role": "system", "content": self.sys_prompt})
        for message in self.messages:
            conversation.append({"role": message.role, "content": message.content})
        return conversation

    def send_message(self) -> str:
        conversation = self._build_conversation()
        if not conversation:
            raise ValueError("No conversation history available to send")

        body = {
            "messages": conversation,
            "max_tokens": 1024,
            "temperature": 0.7,
        }
        try:
            response = self.client.invoke_model(
                modelId="openai.gpt-oss-120b-1:0",
                contentType="application/json",
                accept="application/json",
                body=json.dumps(body),
            )
        except Exception as e:
            return f"Bedrock service is currently unavailable. {e}"

        result = json.loads(response["body"].read())
        reply = result["choices"][0]["message"]["content"]
        self.messages.append(Message(role="assistant", content=reply))
        return reply


class OrchestratorAgent(BaseModel):
    db_pool: Any


