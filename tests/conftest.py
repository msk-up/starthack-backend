import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock


# Mock record class to simulate asyncpg.Record
class MockRecord(dict):
    def __getattr__(self, name):
        return self.get(name)


@pytest.fixture
def mock_db_pool():
    pool = AsyncMock()
    connection = AsyncMock()

    # Setup connection context manager
    pool.acquire.return_value.__aenter__.return_value = connection
    pool.acquire.return_value.__aexit__.return_value = None

    # Common fetch/execute mocks
    connection.fetch.return_value = []
    connection.execute.return_value = None

    # Allow pool to be used directly like execute/fetch if the app does that
    pool.fetch = connection.fetch
    pool.execute = connection.execute

    return pool


@pytest.fixture
def mock_bedrock_client():
    client = MagicMock()
    return client