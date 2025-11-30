from typing import Any, Callable, Coroutine
from dataclasses import dataclass, field
import asyncio
import logging

from agents import NegotiationAgent, OrchestratorAgent

logger = logging.getLogger("negotiation.router")


@dataclass
class EmailEvent:
    """Represents an incoming email event. Extend fields as needed for your provider."""

    sender: str
    subject: str
    body: str
    supplier_id: str | None = None
    ng_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)  # Store provider-specific data


def _make_key(ng_id: str, supplier_id: str) -> str:
    """Create a unique key for a negotiation + supplier pair."""
    return f"{ng_id}:{supplier_id}"


class EmailEventRouter:
    """
    Routes incoming email events to the appropriate handlers.

    Flow:
    1. Register handlers by (ng_id, supplier_id) pair
    2. When email arrives, call push() with EmailEvent
    3. Router looks up handler by composite key and spawns async task
    """

    def __init__(self):
        self._handlers: dict[
            str, Callable[[EmailEvent], Coroutine[Any, Any, None]]
        ] = {}
        self._default_handler: (
            Callable[[EmailEvent], Coroutine[Any, Any, None]] | None
        ) = None

    def register(
        self,
        ng_id: str,
        supplier_id: str,
        handler: Callable[[EmailEvent], Coroutine[Any, Any, None]],
    ) -> None:
        """Register a handler for a specific (ng_id, supplier_id) pair."""
        key = _make_key(ng_id, supplier_id)
        self._handlers[key] = handler
        logger.info(f"Registered handler for key: {key}")

    def set_default_handler(
        self, handler: Callable[[EmailEvent], Coroutine[Any, Any, None]]
    ) -> None:
        """Set a fallback handler for unmatched events."""
        self._default_handler = handler

    def unregister(self, ng_id: str, supplier_id: str) -> None:
        """Remove a handler for a (ng_id, supplier_id) pair."""
        key = _make_key(ng_id, supplier_id)
        self._handlers.pop(key, None)

    async def push(self, event: EmailEvent) -> None:
        """
        Push an email event to be routed.
        Looks up handler by (ng_id, supplier_id) and spawns async task.
        """
        logger.info(
            f"Router.push() called with ng_id={event.ng_id}, supplier_id={event.supplier_id}"
        )
        logger.info(f"Available handler keys: {list(self._handlers.keys())}")

        handler = None

        if event.ng_id and event.supplier_id:
            key = _make_key(event.ng_id, event.supplier_id)
            logger.info(f"Looking up handler for key: {key}")
            handler = self._handlers.get(key)
            if handler:
                logger.info(f"Found handler for key: {key}")
            else:
                logger.warning(f"No handler found for key: {key}")

        if not handler and self._default_handler:
            logger.info("Using default handler")
            handler = self._default_handler

        if handler:
            logger.info("Spawning async task for handler...")
            asyncio.create_task(handler(event))
            logger.info("Handler task spawned")
        else:
            logger.warning(
                f"No handler found for event - ng_id={event.ng_id}, supplier_id={event.supplier_id}"
            )


class NegotiationSession:
    """
    Manages a full negotiation session with an orchestrator and multiple supplier agents.
    Registers email handlers so incoming supplier emails trigger:
      1. Orchestrator revises strategy
      2. Appropriate agent responds
    """

    def __init__(
        self,
        db_pool: Any,
        client: Any,
        ng_id: str,
        orchestrator: OrchestratorAgent,
        router: EmailEventRouter,
    ):
        self.db_pool = db_pool
        self.client = client
        self.ng_id = ng_id
        self.orchestrator = orchestrator
        self.router = router
        self._agents: dict[str, NegotiationAgent] = {}

    def add_agent(self, supplier_id: str, agent: NegotiationAgent) -> None:
        """Add a negotiation agent and register its email handler."""
        self._agents[supplier_id] = agent
        self.router.register(self.ng_id, supplier_id, self._make_handler(supplier_id))

    def _make_handler(
        self, supplier_id: str
    ) -> Callable[[EmailEvent], Coroutine[Any, Any, None]]:
        """Create an email handler for a specific supplier."""

        async def handler(event: EmailEvent) -> None:
            logger.info(
                f"[Session {self.ng_id}] Handler triggered for supplier {supplier_id}"
            )
            logger.info(f"[Session {self.ng_id}] Email subject: {event.subject}")
            logger.info(
                f"[Session {self.ng_id}] Email body preview: {event.body[:200] if event.body else '(empty)'}..."
            )

            # 1. Store the incoming message in DB
            logger.info(
                f"[Session {self.ng_id}] Saving supplier message to database..."
            )
            await self.db_pool.execute(
                """
                INSERT INTO message (ng_id, supplier_id, role, message_text)
                VALUES ($1, $2, $3, $4)
                """,
                self.ng_id,
                supplier_id,
                "supplier",
                event.body,
            )
            logger.info(f"[Session {self.ng_id}] Supplier message saved")

            # 2. Orchestrator revises strategy for all agents
            logger.info(
                f"[Session {self.ng_id}] Calling orchestrator.generate_new_instructions()..."
            )
            await self.orchestrator.generate_new_instructions()
            logger.info(f"[Session {self.ng_id}] Orchestrator instructions updated")

            # 3. Agent for this supplier sends response
            agent = self._agents.get(supplier_id)
            if agent:
                logger.info(
                    f"[Session {self.ng_id}] Calling agent.send_message() for supplier {supplier_id}..."
                )
                await agent.send_message()
                logger.info(f"[Session {self.ng_id}] Agent response sent")
            else:
                logger.error(
                    f"[Session {self.ng_id}] No agent found for supplier {supplier_id}"
                )

        return handler

    def cleanup(self) -> None:
        """Unregister all handlers when session ends."""
        for supplier_id in self._agents:
            self.router.unregister(self.ng_id, supplier_id)
