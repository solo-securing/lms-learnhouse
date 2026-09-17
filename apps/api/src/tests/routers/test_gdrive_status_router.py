"""Router tests for src/routers/integrations/gdrive.py (GET /status)."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.db.users import AnonymousUser
from src.routers.integrations.gdrive import router as gdrive_router
from src.security.auth import get_current_user
from src.services.integrations.gdrive.readiness import GDriveStatus


def _app(user):
    app = FastAPI()
    app.include_router(gdrive_router, prefix="/api/v1/integrations/gdrive")
    app.dependency_overrides[get_current_user] = lambda: user
    return app


@pytest.fixture
async def client(admin_user):
    app = _app(admin_user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
async def anon_client():
    app = _app(AnonymousUser())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


class TestGDriveStatusRouter:
    async def test_401_when_not_signed_in(self, anon_client):
        with patch("src.routers.integrations.gdrive.get_status", new_callable=AsyncMock) as get_status:
            response = await anon_client.get("/api/v1/integrations/gdrive/status")
        assert response.status_code == 401
        get_status.assert_not_awaited()

    @pytest.mark.parametrize(
        "status,expected",
        [
            (GDriveStatus(enabled=True, ready=True, reason=None), {"enabled": True, "ready": True, "reason": None}),
            (GDriveStatus(enabled=False, ready=False, reason="disabled"), {"enabled": False, "ready": False, "reason": "disabled"}),
            (
                GDriveStatus(enabled=True, ready=False, reason="needs_reauthorization"),
                {"enabled": True, "ready": False, "reason": "needs_reauthorization"},
            ),
        ],
    )
    async def test_200_shape(self, client, status, expected):
        with patch("src.routers.integrations.gdrive.get_status", new_callable=AsyncMock, return_value=status):
            response = await client.get("/api/v1/integrations/gdrive/status")
        assert response.status_code == 200
        body = response.json()
        assert body == expected
        # Nothing but the three contract keys — no email, paths or quota.
        assert set(body) == {"enabled", "ready", "reason"}
