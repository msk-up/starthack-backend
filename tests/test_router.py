import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from router import EmailEventRouter, EmailEvent, NegotiationSession


@pytest.mark.asyncio
async def test_router_registration_and_push():
    router = EmailEventRouter()
    mock_handler = AsyncMock()

    ng_id = "ng-123"
    sup_id = "sup-456"

    # Register handler
    router.register(ng_id, sup_id, mock_handler)

    # Create event
    event = EmailEvent(sender="test@test.com", subject="Hi", body="Hello", ng_id=ng_id, supplier_id=sup_id)

    # Push event
    await router.push(event)

    # Allow async task to run
    await asyncio.sleep(0.01)

    mock_handler.assert_called_once_with(event)


@pytest.mark.asyncio
async def test_router_default_handler():
    router = EmailEventRouter()
    mock_default = AsyncMock()
    router.set_default_handler(mock_default)

    # Event with no matching ID
    event = EmailEvent(sender="test@test.com", subject="Hi", body="Hello")

    await router.push(event)
    await asyncio.sleep(0.01)

    mock_default.assert_called_once_with(event)


@pytest.mark.asyncio
async def test_negotiation_session_flow(mock_db_pool):
    # Setup
    client = MagicMock()
    orchestrator = AsyncMock()
    orchestrator.generate_new_instructions = AsyncMock()

    router = EmailEventRouter()
    session = NegotiationSession(mock_db_pool, client, "ng-1", orchestrator, router)

    # Mock Agent
    agent = AsyncMock()
    agent.send_message = AsyncMock()
    session.add_agent("sup-1", agent)

    # Simulate routing logic manually triggering the handler created by session
    # We need to find the handler the session registered
    key = "ng-1:sup-1"
    handler = router._handlers[key]

    event = EmailEvent(sender="sup@ex.com", subject="Offer", body="Price is 100", ng_id="ng-1", supplier_id="sup-1")

    await handler(event)

    # Assertions
    # 1. DB Inserted message
    mock_db_pool.execute.assert_called()
    args = mock_db_pool.execute.call_args_list[0]
    assert "INSERT INTO message" in args[0][0]
    assert "Price is 100" in args[0]

    # 2. Orchestrator called
    orchestrator.generate_new_instructions.assert_called_once()

    # 3. Agent responded
    agent.send_message.assert_called_once()

    # Cleanup
    session.cleanup()
    assert key not in router._handlers