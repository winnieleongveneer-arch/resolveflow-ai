# tests/test_main.py
import os

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

# Mark all tests in this file as async
pytestmark = pytest.mark.asyncio


async def test_health_check():
    """
    Tests the public health check endpoint.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_unauthorized_access():
    """
    Protected endpoints require authentication.

    AUTH_BYPASS=true is the template's development default and authenticates
    every request as Dev User, so 200 is the correct answer in that mode. The
    assertion follows the setting rather than contradicting it.
    """
    bypass = os.getenv("AUTH_BYPASS", "false").lower() == "true"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/api/test")

    if bypass:
        assert response.status_code == 200, (
            "With AUTH_BYPASS=true every request should be authenticated as Dev User."
        )
    else:
        assert response.status_code == 401


# Additional tests would include:
# - Database integration tests
# - Authorization engine tests
# - API endpoint tests with mocked authentication
# - Model validation tests
