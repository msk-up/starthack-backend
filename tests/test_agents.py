import pytest
import json
from unittest.mock import MagicMock, AsyncMock
from agents import NegotiationAgent, OrchestratorAgent
from tests.conftest import MockRecord


@pytest.mark.asyncio
async def test_negotiation_agent_send_message(mock_db_pool, mock_bedrock_client):
    # Setup
    agent = NegotiationAgent(mock_db_pool, mock_bedrock_client, "sys_prompt", "ng-1", "sup-1", "Widgets")

    # Mock DB history
    mock_db_pool.fetch.side_effect = [
        [MockRecord(role="user", content="Previous msg")],  # messages
        [MockRecord(content="Be tough")]  # instructions
    ]

    # Mock Bedrock Response
    mock_response_body = json.dumps({
        "choices": [{"message": {"content": "I offer $50"}}]
    })
    mock_bedrock_client.invoke_model.return_value = {"body": MagicMock(read=lambda: mock_response_body)}

    # Execute
    reply = await agent.send_message()

    # Verify
    assert reply == "I offer $50"

    # Verify Bedrock call payload
    call_kwargs = mock_bedrock_client.invoke_model.call_args[1]
    body = json.loads(call_kwargs['body'])
    assert len(body['messages']) > 0
    assert body['messages'][0]['content'] == "sys_prompt"

    # Verify DB update
    mock_db_pool.execute.assert_called()


@pytest.mark.asyncio
async def test_orchestrator_generate_instructions(mock_db_pool, mock_bedrock_client):
    # Setup
    orch = OrchestratorAgent(
        db_pool=mock_db_pool,
        sys_promt="Sys",  # Note: typo in class definition being matched here
        strategy="Win",
        product="Widget",
        ng_id="ng-1",
        client=mock_bedrock_client
    )

    # Mock DB Data
    # 1. All messages
    mock_db_pool.fetch.side_effect = [
        [
            MockRecord(ng_id="ng-1", supplier_id="sup-1", role="supplier", message_text="Hi",
                       message_timestamp=MagicMock(isoformat=lambda: "2023-01-01")),
        ],
        # 2. Existing instructions
        [],
        # 3. Messages for _build_conversation context (Orchestrator history)
        []
    ]

    # Mock LLM Response with strict Regex format required
    llm_content = """
    Here is the plan:
    [INSTRUCTION]
    ng_id: ng-1
    supplier_id: sup-1
    text: Offer 10% less
    [/INSTRUCTION]
    """

    mock_response_body = json.dumps({
        "choices": [{"message": {"content": llm_content}}]
    })
    mock_bedrock_client.invoke_model.return_value = {"body": MagicMock(read=lambda: mock_response_body)}

    # Execute
    await orch.generate_new_instructions()

    # Verify DB Insert
    mock_db_pool.execute.assert_called()
    call_args = mock_db_pool.execute.call_args[0]
    assert "INSERT INTO instructions" in call_args[0]
    assert "Offer 10% less" in call_args