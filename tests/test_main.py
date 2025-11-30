import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock
from main import app
from tests.conftest import MockRecord


@pytest.fixture
def client(mock_db_pool):
    # Patch 'main.get_pool' so direct calls in endpoints return our mock pool
    # Patch 'asyncpg.create_pool' so the lifespan startup doesn't try to connect to real DB
    with patch("main.get_pool", new_callable=AsyncMock) as mock_get_pool, \
            patch("asyncpg.create_pool", new_callable=AsyncMock) as mock_create_pool:
        mock_get_pool.return_value = mock_db_pool
        mock_create_pool.return_value = mock_db_pool  # Lifespan will get this mock

        with TestClient(app) as test_client:
            yield test_client


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_suppliers_endpoint(client, mock_db_pool):
    # Setup mock return data
    mock_db_pool.fetch.return_value = [
        MockRecord(supplier_id="1", supplier_name="ACME", description="desc")
    ]

    response = client.get("/suppliers")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["supplier_name"] == "ACME"


@pytest.mark.asyncio
async def test_negotiate_start(client, mock_db_pool):
    with patch("main.OrchestratorAgent") as MockOrch, \
            patch("main.NegotiationSession") as MockSession, \
            patch("main.NegotiationAgent") as MockAgent:
        mock_db_pool.execute.return_value = None

        payload = {
            "product": "Widgets",
            "prompt": "Buy cheap",
            "tactics": "Aggressive",
            "suppliers": ["sup-1", "sup-2"]
        }

        response = client.post("/negotiate", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "started"
        assert "negotiation_id" in data
        assert MockAgent.call_count == 2