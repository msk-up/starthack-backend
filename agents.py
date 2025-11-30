from typing import Any
import re
import json
import logging
from pydantic import BaseModel

logger = logging.getLogger("negotiation.agents")


def strip_reasoning_tokens(text: str) -> str:
    """
    Remove reasoning/thinking tokens from model output before sending to email.
    Handles common patterns like <thinking>, <reasoning>, <scratchpad>, etc.
    """
    # Remove content within common reasoning tags
    patterns = [
        r"<thinking>.*?</thinking>",
        r"<reasoning>.*?</reasoning>",
        r"<scratchpad>.*?</scratchpad>",
        r"<think>.*?</think>",
        r"<reflection>.*?</reflection>",
        r"<internal>.*?</internal>",
        r"<analysis>.*?</analysis>",
    ]

    result = text
    for pattern in patterns:
        result = re.sub(pattern, "", result, flags=re.DOTALL | re.IGNORECASE)

    # Clean up extra whitespace left behind
    result = re.sub(r"\n{3,}", "\n\n", result)
    result = result.strip()

    return result


class Message(BaseModel):
    role: str
    content: str
    timestamp: str | None = None


class NegotiationAgent:
    def __init__(
        self,
        db_pool: Any,
        client: Any,
        sys_prompt: str,
        ng_id: str,
        sup_id: str,
        product: str,
        email_client: Any = None,
        supplier_email: str | None = None,
        supplier_name: str = "Supplier",
        supplier_insights: str = "",
    ) -> None:
        self.client = client
        self.db_pool = db_pool
        self.sys_prompt = sys_prompt
        self.product = product
        self.ng_id = ng_id
        self.sup_id = sup_id
        self.email_client = email_client
        self.supplier_email = supplier_email
        self.supplier_name = supplier_name
        self.supplier_insights = supplier_insights

    def _map_role(self, db_role: str) -> str:
        """Map database roles to API-compatible roles."""
        role_mapping = {
            "negotiator": "assistant",
            "supplier": "user",
            "system": "system",
            "user": "user",
            "assistant": "assistant",
        }
        return role_mapping.get(db_role, "user")

    async def _build_conversation(self) -> list[dict[str, str]]:
        conversation: list[dict[str, str]] = []
        if self.sys_prompt:
            conversation.append({"role": "system", "content": self.sys_prompt})
        messages = await self.db_pool.fetch(
            "SELECT * FROM message WHERE ng_id = $1 AND supplier_id = $2 ORDER BY message_timestamp",
            self.ng_id,
            self.sup_id,
        )
        for message in messages:
            api_role = self._map_role(message["role"])
            conversation.append({"role": api_role, "content": message["message_text"]})
        instructions = await self.db_pool.fetch(
            "SELECT * FROM instructions WHERE ng_id = $1 AND supplier_id = $2",
            self.ng_id,
            self.sup_id,
        )
        for instruction in instructions:
            conversation.append(
                {
                    "role": "system",
                    "content": f"Supervisor instruction: {instruction['instructions']}",
                }
            )
        return conversation

    async def send_initial_message(self, context: str = "") -> str:
        """
        Send the first message to initiate negotiation with the supplier.
        This asks about possible offers for the product.
        """
        logger.info(
            f"[Agent {self.ng_id}:{self.sup_id}] Preparing initial message for product: {self.product}"
        )

        conversation: list[dict[str, str]] = []
        if self.sys_prompt:
            conversation.append({"role": "system", "content": self.sys_prompt})

        # Build insights section if available
        insights_section = ""
        if self.supplier_insights:
            insights_section = f"""
Background information about this supplier:
{self.supplier_insights}

Use this information strategically in your negotiation approach.
"""

        # Add context about what we're negotiating
        initial_prompt = f"""You are initiating a negotiation with {self.supplier_name} for: {self.product}

{f"Additional context: {context}" if context else ""}
{insights_section}
Write a professional opening message addressed to {self.supplier_name} asking about:
- Their available offerings for this product
- Current pricing and volume discounts
- Lead times and delivery options
- Any ongoing promotions or deals

Be polite, professional, and express genuine interest in establishing a business relationship.
Address the supplier by name ({self.supplier_name}) in your message."""

        conversation.append({"role": "user", "content": initial_prompt})

        body = {
            "messages": conversation,
            "max_tokens": 1024,
            "temperature": 0.7,
        }

        logger.info(f"[Agent {self.ng_id}:{self.sup_id}] Calling Bedrock model...")
        try:
            response = self.client.invoke_model(
                modelId="openai.gpt-oss-120b-1:0",
                contentType="application/json",
                accept="application/json",
                body=json.dumps(body),
            )
        except Exception as e:
            logger.error(f"[Agent {self.ng_id}:{self.sup_id}] Bedrock call failed: {e}")
            return f"Bedrock service is currently unavailable. {e}"

        result = json.loads(response["body"].read())
        reply = result["choices"][0]["message"]["content"]
        logger.info(
            f"[Agent {self.ng_id}:{self.sup_id}] Bedrock response received ({len(reply)} chars)"
        )

        # Save the initial message to DB
        logger.info(f"[Agent {self.ng_id}:{self.sup_id}] Saving message to database...")
        await self.db_pool.execute(
            "INSERT INTO message (ng_id, supplier_id, role, message_text) VALUES ($1, $2, $3, $4)",
            self.ng_id,
            self.sup_id,
            "negotiator",
            reply,
        )
        logger.info(f"[Agent {self.ng_id}:{self.sup_id}] Message saved to database")

        # Send via email if configured
        if self.email_client and self.supplier_email:
            logger.info(
                f"[Agent {self.ng_id}:{self.sup_id}] Sending email to {self.supplier_email}..."
            )
            # Strip reasoning tokens before sending email
            email_body = strip_reasoning_tokens(reply)
            # Include supplier name and ref IDs in subject for tracking replies
            subject = f"[{self.supplier_name}] [REF-{self.ng_id[:8]}-{self.sup_id[:8]}] Inquiry about {self.product}"
            await self.email_client.email_send(self.supplier_email, subject, email_body)
            logger.info(f"[Agent {self.ng_id}:{self.sup_id}] Email sent successfully")
        else:
            logger.warning(
                f"[Agent {self.ng_id}:{self.sup_id}] Email not sent - no email client or supplier email configured"
            )

        return reply

    async def send_message(self) -> str:
        logger.info(
            f"[Agent {self.ng_id}:{self.sup_id}] Building conversation for response..."
        )
        conversation = await self._build_conversation()
        if not conversation:
            logger.warning(
                f"[Agent {self.ng_id}:{self.sup_id}] No conversation history available"
            )
            pass

        body = {
            "messages": conversation,
            "max_tokens": 1024,
            "temperature": 0.7,
        }

        logger.info(f"[Agent {self.ng_id}:{self.sup_id}] Calling Bedrock model...")
        try:
            response = self.client.invoke_model(
                modelId="openai.gpt-oss-120b-1:0",
                contentType="application/json",
                accept="application/json",
                body=json.dumps(body),
            )
        except Exception as e:
            logger.error(f"[Agent {self.ng_id}:{self.sup_id}] Bedrock call failed: {e}")
            return f"Bedrock service is currently unavailable. {e}"

        result = json.loads(response["body"].read())
        reply = result["choices"][0]["message"]["content"]
        logger.info(
            f"[Agent {self.ng_id}:{self.sup_id}] Bedrock response received ({len(reply)} chars)"
        )

        Message(role="assistant", content=reply)

        logger.info(f"[Agent {self.ng_id}:{self.sup_id}] Saving message to database...")
        await self.db_pool.execute(
            "INSERT INTO message (ng_id, supplier_id, role, message_text) VALUES ($1, $2, $3, $4)",
            self.ng_id,
            self.sup_id,
            "negotiator",
            reply,
        )
        logger.info(f"[Agent {self.ng_id}:{self.sup_id}] Message saved to database")

        # Send via email if configured
        if self.email_client and self.supplier_email:
            logger.info(
                f"[Agent {self.ng_id}:{self.sup_id}] Sending email to {self.supplier_email}..."
            )
            # Strip reasoning tokens before sending email
            email_body = strip_reasoning_tokens(reply)
            # Include supplier name and ref IDs in subject for tracking replies
            subject = f"Re: [{self.supplier_name}] [REF-{self.ng_id[:8]}-{self.sup_id[:8]}] {self.product} negotiation"
            await self.email_client.email_send(self.supplier_email, subject, email_body)
            logger.info(f"[Agent {self.ng_id}:{self.sup_id}] Email sent successfully")
        else:
            logger.warning(
                f"[Agent {self.ng_id}:{self.sup_id}] Email not sent - no email client or supplier email configured"
            )

        return reply


class OrchestratorAgent:
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
            "SELECT * FROM message WHERE ng_id = $1 AND supplier_id IS NULL",
            self.ng_id,
        )
        for message in messages:
            conversation.append({"role": message.role, "content": message.content})
        return conversation

    async def generate_new_instructions(self) -> None:
        """
        Reads messages for THIS negotiation only, groups by supplier,
        then asks the model to revise instructions for each supplier agent.
        Updates the instructions table.
        """
        logger.info(f"[Orchestrator {self.ng_id}] Generating new instructions...")

        # 1. Fetch messages for THIS negotiation only
        all_messages = await self.db_pool.fetch(
            "SELECT ng_id, supplier_id, role, message_text, message_timestamp "
            "FROM message WHERE ng_id = $1 ORDER BY message_timestamp",
            self.ng_id,
        )
        logger.info(f"[Orchestrator {self.ng_id}] Found {len(all_messages)} messages")

        # 2. Group messages by supplier_id
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in all_messages:
            sup_id = str(row["supplier_id"]) if row["supplier_id"] else "orchestrator"
            if sup_id not in grouped:
                grouped[sup_id] = []
            grouped[sup_id].append(
                {
                    "supplier_id": sup_id,
                    "role": row["role"],
                    "text": row["message_text"],
                    "timestamp": row["message_timestamp"].isoformat()
                    if row["message_timestamp"]
                    else None,
                }
            )

        # 3. Fetch existing instructions for THIS negotiation
        existing_instructions: dict[str, str] = {}  # supplier_id -> instructions
        instr_rows = await self.db_pool.fetch(
            "SELECT supplier_id, instructions FROM instructions WHERE ng_id = $1",
            self.ng_id,
        )
        for row in instr_rows:
            sup_id = str(row["supplier_id"])
            existing_instructions[sup_id] = row["instructions"]

        # 4. Build the orchestrator prompt
        conversation = await self._build_conversation()

        # Build context for each supplier in this negotiation
        agent_context_parts = []
        valid_pairs = []

        for sup_id, messages in grouped.items():
            if sup_id == "orchestrator":
                continue  # Skip orchestrator messages

            part = f"## Supplier: {sup_id}\n"
            valid_pairs.append(f"  - ng_id: {self.ng_id}, supplier_id: {sup_id}")

            # Include existing instructions if any
            if sup_id in existing_instructions:
                part += f"### Current Instructions:\n{existing_instructions[sup_id]}\n"

            part += "### Message History (chronological):\n"
            for msg in messages:
                part += f"[{msg['timestamp']}] {msg['role']}: {msg['text']}\n"
            agent_context_parts.append(part)

        full_context = "\n\n".join(agent_context_parts)
        valid_pairs_str = "\n".join(valid_pairs)

        logger.info(
            f"[Orchestrator {self.ng_id}] Processing {len(valid_pairs)} supplier(s)"
        )

        conversation.append(
            {
                "role": "user",
                "content": f"""New messages have arrived from a supplier in this negotiation.
Review the conversation history below and provide revised instructions for the negotiation agent.

Strategy reminder: {self.strategy}

{full_context}

IMPORTANT: You MUST use the EXACT UUIDs from the list below. Do NOT use placeholders.

Valid negotiation/supplier pairs:
IMPORTANT: Use these EXACT UUIDs:
{valid_pairs_str}

Respond with revised instructions using EXACTLY this format (one block per supplier):

[INSTRUCTION]
ng_id: {self.ng_id}
supplier_id: <copy the exact supplier_id UUID from above>
text: <your concise, actionable instruction - 2-3 sentences max>
[/INSTRUCTION]

RULES:
- Use the exact UUIDs provided above, do NOT use placeholders
- Keep instructions brief and actionable
- Do NOT include reasoning or explanation outside the [INSTRUCTION] blocks
- Start your response directly with [INSTRUCTION]""",
            }
        )

        # 5. Call the model
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
        import uuid as uuid_module

        for match in matches:
            parsed_ng_id = match[0].strip()
            parsed_supplier_id = match[1].strip()
            instructions_text = match[2].strip()

            if not all([parsed_ng_id, parsed_supplier_id, instructions_text]):
                logger.warning(f"Skipping incomplete instruction block")
                continue

            # Validate UUIDs before inserting
            try:
                uuid_module.UUID(parsed_ng_id)
                uuid_module.UUID(parsed_supplier_id)
            except ValueError:
                logger.warning(
                    f"Skipping invalid UUIDs: ng_id={parsed_ng_id}, supplier_id={parsed_supplier_id}"
                )
                continue

            logger.info(
                f"Upserting instruction for ng_id={parsed_ng_id}, supplier_id={parsed_supplier_id}"
            )
            await self.db_pool.execute(
                """
                INSERT INTO instructions (supplier_id, ng_id, instructions)
                VALUES ($1, $2, $3)
                ON CONFLICT (supplier_id, ng_id) 
                DO UPDATE SET instructions = $3
                """,
                parsed_supplier_id,
                parsed_ng_id,
                instructions_text,
            )
