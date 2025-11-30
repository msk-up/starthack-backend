from typing import Any, Callable, Coroutine
from dataclasses import dataclass, field
import asyncio

from agents import NegotiationAgent, OrchestratorAgent


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
        handler = None

        if event.ng_id and event.supplier_id:
            key = _make_key(event.ng_id, event.supplier_id)
            handler = self._handlers.get(key)

        if not handler and self._default_handler:
            handler = self._default_handler

        if handler:
            asyncio.create_task(handler(event))


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
            # 1. Store the incoming message in DB
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

            # 2. Orchestrator revises strategy for all agents
            await self.orchestrator.generate_new_instructions()

            # 3. Agent for this supplier sends response
            agent = self._agents.get(supplier_id)
            if agent:
                await agent.send_message()

        return handler

    def cleanup(self) -> None:
        """Unregister all handlers when session ends."""
        for supplier_id in self._agents:
            self.router.unregister(self.ng_id, supplier_id)
