from typing import Any
import re
import json
from pydantic import BaseModel


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
        ng_id: str,
        sup_id: str,
        product: str,
    ) -> None:
        self.client = client
        self.db_pool = db_pool
        self.sys_prompt = sys_prompt
        self.product = product
        self.ng_id = ng_id
        self.sup_id = sup_id

    async def _build_conversation(self) -> list[dict[str, str]]:
        conversation: list[dict[str, str]] = []
        if self.sys_prompt:
            conversation.append({"role": "system", "content": self.sys_prompt})
        messages = await self.db_pool.fetch(
            "SELECT * FROM messages WHERE ng_id = $1 AND supplier_id  = $2",
            self.ng_id,
            self.sup_id,
        )
        for message in messages:
            conversation.append({"role": message.role, "content": message.content})
        instructions = await self.db_pool.fetch(
            "SELECT * FROM instructions WHERE ng_id = $1 AND supplier_id = $3",
            self.ng_id,
        )
        for instruction in instructions:
            conversation.append({"role": "supervisor", "content": instruction.content})
        return conversation

    async def send_message(self) -> str:
        conversation = self._build_conversation()
        if not conversation:
            # raise ValueError("No conversation history available to send")
            pass

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
        Message(role="assistant", content=reply)
        await self.db_pool.execute(
            "INSERT INTO messages (ng_id, supplier_id, role, content) VALUES ($1, $2, $3, $4)",
            self.ng_id,
            self.sup_id,
            "negotiator",
            reply,
        )
        return reply


##class Summarize():


class OrchestratorAgent(BaseModel):
    db_pool: Any
    client: Any
    sys_prompt: str
    strategy: str
    ng_id: str
    product: str

    def __init__(
        self,
        db_pool: Any,
        sys_promt: str,
        strategy: str,
        product: str,
        ng_id: str,
        client: Any,
    ) -> None:
        self.db_pool = db_pool
        self.sys_prompt = sys_promt
        self.strategy = strategy
        self.product = product
        self.client = client
        self.ng_id = ng_id
        return

    async def _build_conversation(self) -> list[dict[str, str]]:
        conversation: list[dict[str, str]] = []
        if self.sys_prompt:
            conversation.append({"role": "system", "content": self.sys_prompt})
        conversation.append({"role": "user", "content": self.strategy})

        messages = await self.db_pool.fetch(
            "SELECT * FROM messages WHERE ng_id = $1 AND supplier_id IS NULL",
            self.ng_id,
        )
        for message in messages:
            conversation.append({"role": message.role, "content": message.content})
        return conversation

    async def generate_new_instructions(self) -> None:
        """
        Reads all messages from all agents, groups them by agent (ng_id),
        sorts each group by timestamp, then asks the model to revise
        instructions for each sub-agent. Updates the instructions table.
        """
        # 1. Fetch all messages ordered by agent and time
        all_messages = await self.db_pool.fetch(
            "SELECT ng_id, supplier_id, role, message_text, message_timestamp "
            "FROM message ORDER BY ng_id, message_timestamp"
        )

        # 2. Group messages by ng_id
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in all_messages:
            ng_id = str(row["ng_id"])
            if ng_id not in grouped:
                grouped[ng_id] = []
            grouped[ng_id].append(
                {
                    "supplier_id": str(row["supplier_id"]),
                    "role": row["role"],
                    "text": row["message_text"],
                    "timestamp": row["message_timestamp"].isoformat()
                    if row["message_timestamp"]
                    else None,
                }
            )

        # 3. Fetch existing instructions for context
        existing_instructions: dict[
            str, dict[str, str]
        ] = {}  # ng_id -> {supplier_id -> instructions}
        instr_rows = await self.db_pool.fetch(
            "SELECT supplier_id, ng_id, instructions FROM instructions"
        )
        for row in instr_rows:
            ng_id = str(row["ng_id"])
            sup_id = str(row["supplier_id"])
            if ng_id not in existing_instructions:
                existing_instructions[ng_id] = {}
            existing_instructions[ng_id][sup_id] = row["instructions"]

        # 4. Build the orchestrator prompt
        conversation = await self._build_conversation()

        # Add context about each agent's messages and current instructions
        agent_context_parts = []
        for ng_id, messages in grouped.items():
            part = f"## Agent {ng_id}\n"

            # Include existing instructions if any
            if ng_id in existing_instructions:
                part += "### Current Instructions:\n"
                for sup_id, instr in existing_instructions[ng_id].items():
                    part += f"- Supplier {sup_id}: {instr}\n"

            part += "### Message History (chronological):\n"
            for msg in messages:
                part += f"[{msg['timestamp']}] {msg['role']}: {msg['text']}\n"
            agent_context_parts.append(part)

        full_context = "\n\n".join(agent_context_parts)

        conversation.append(
            {
                "role": "user",
                "content": f"""New messages have arrived from suppliers to the negotiation agents. 
Review the conversation history below and revise the strategy/instructions for each agent.

Respect and build upon the existing instructions where appropriate, but update them based on 
the latest negotiation progress.

{full_context}

Respond with revised instructions for each agent using EXACTLY this format (one block per agent-supplier pair):

[INSTRUCTION]
ng_id: <the agent uuid>
supplier_id: <the supplier uuid>
text: <your revised instruction text here>
[/INSTRUCTION]

You MUST use this exact format. Do not use JSON. Do not add any text outside the [INSTRUCTION] blocks.
Focus on actionable guidance that helps each agent negotiate effectively based on the current state.""",
            }
        )

        # 5. Call the model
        body = {
            "messages": conversation,
            "max_tokens": 2048,
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
            raise RuntimeError(f"Bedrock service is currently unavailable: {e}")

        result = json.loads(response["body"].read())
        reply = result["choices"][0]["message"]["content"]

        # 6. Parse the model response using regex for [INSTRUCTION] blocks
        pattern = re.compile(
            r"\[INSTRUCTION\]\s*"
            r"ng_id:\s*(?P<ng_id>[^\n]+)\s*"
            r"supplier_id:\s*(?P<supplier_id>[^\n]+)\s*"
            r"text:\s*(?P<text>.*?)"
            r"\[/INSTRUCTION\]",
            re.DOTALL | re.IGNORECASE,
        )

        matches = pattern.findall(reply)
        if not matches:
            raise RuntimeError(
                f"No valid [INSTRUCTION] blocks found in response:\n{reply}"
            )

        # 7. Upsert instructions for each agent
        for match in matches:
            ng_id = match[0].strip()
            supplier_id = match[1].strip()
            instructions_text = match[2].strip()

            if not all([ng_id, supplier_id, instructions_text]):
                continue

            await self.db_pool.execute(
                """
                INSERT INTO instructions (supplier_id, ng_id, instructions)
                VALUES ($1, $2, $3)
                ON CONFLICT (supplier_id, ng_id) 
                DO UPDATE SET instructions = $3
                """,
                supplier_id,
                ng_id,
                instructions_text,
            )
