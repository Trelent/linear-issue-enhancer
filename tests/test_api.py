"""Tests for API startup and webhook handling (without LLM calls)."""

import os
import pytest
from unittest.mock import patch, AsyncMock

# Ensure env vars are set before importing
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("LINEAR_API_KEY", "test-key")


class TestHealthEndpoint:
    """Tests for the /health endpoint."""

    @pytest.mark.asyncio
    async def test_health_returns_ok(self):
        from fastapi.testclient import TestClient
        from src.api import app
        
        # Use TestClient with context manager to handle lifespan
        with patch("src.api.sync_all_async", new_callable=AsyncMock) as mock_sync:
            mock_sync.return_value = False
            with TestClient(app, raise_server_exceptions=False) as client:
                response = client.get("/health")
        
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestWebhookSkipLogic:
    """Tests for webhook skip conditions (excluded projects, skip tag, etc.)."""

    @pytest.mark.asyncio
    async def test_skip_tag_in_description(self):
        """Issues with [skip=true] in description should be skipped."""
        from fastapi.testclient import TestClient
        from src.api import app
        
        payload = {
            "action": "create",
            "type": "Issue",
            "data": {
                "id": "issue-123",
                "title": "Test Issue",
                "description": "Some notes [skip=true] more text",
                "project": None,
                "team": None,
            }
        }
        
        with patch("src.api.sync_all_async", new_callable=AsyncMock) as mock_sync:
            mock_sync.return_value = False
            with TestClient(app) as client:
                response = client.post("/webhook/linear", json=payload)
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "skipped"
        assert data["reason"] == "Skip tag present"

    @pytest.mark.asyncio
    async def test_excluded_project_is_skipped(self, monkeypatch):
        """Issues in excluded projects should be skipped."""
        monkeypatch.setenv("LINEAR_EXCLUDED_PROJECTS", "Internal,Admin")
        
        # Need to reload the module to pick up the new env var
        import importlib
        from src import api
        importlib.reload(api)
        
        from fastapi.testclient import TestClient
        
        payload = {
            "action": "create",
            "type": "Issue",
            "data": {
                "id": "issue-456",
                "title": "Internal Task",
                "description": "Do something",
                "project": {"name": "Internal"},
                "team": None,
            }
        }
        
        with patch.object(api, "sync_all_async", new_callable=AsyncMock) as mock_sync:
            mock_sync.return_value = False
            with TestClient(api.app) as client:
                response = client.post("/webhook/linear", json=payload)
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "skipped"
        assert "excluded" in data["reason"].lower()

    @pytest.mark.asyncio
    async def test_excluded_project_case_insensitive(self, monkeypatch):
        """Project exclusion should be case-insensitive."""
        monkeypatch.setenv("LINEAR_EXCLUDED_PROJECTS", "internal")
        
        import importlib
        from src import api
        importlib.reload(api)
        
        from fastapi.testclient import TestClient
        
        payload = {
            "action": "create",
            "type": "Issue",
            "data": {
                "id": "issue-789",
                "title": "Some Task",
                "description": "Details",
                "project": {"name": "INTERNAL"},  # uppercase
                "team": None,
            }
        }
        
        with patch.object(api, "sync_all_async", new_callable=AsyncMock) as mock_sync:
            mock_sync.return_value = False
            with TestClient(api.app) as client:
                response = client.post("/webhook/linear", json=payload)
        
        assert response.status_code == 200
        assert response.json()["status"] == "skipped"

    @pytest.mark.asyncio
    async def test_already_enhanced_is_skipped(self):
        """Issues with enhancement marker should be skipped."""
        from fastapi.testclient import TestClient
        from src.api import app, ENHANCEMENT_MARKER
        
        payload = {
            "action": "create",
            "type": "Issue",
            "data": {
                "id": "issue-enhanced",
                "title": "Already Enhanced",
                "description": f"Some content\n\n{ENHANCEMENT_MARKER}",
                "project": None,
                "team": None,
            }
        }
        
        with patch("src.api.sync_all_async", new_callable=AsyncMock) as mock_sync:
            mock_sync.return_value = False
            with TestClient(app) as client:
                response = client.post("/webhook/linear", json=payload)
        
        assert response.status_code == 200
        assert response.json()["status"] == "skipped"
        assert response.json()["reason"] == "Already enhanced"

    @pytest.mark.asyncio
    async def test_unhandled_event_type_ignored(self):
        """Unhandled event types should return ignored status."""
        from fastapi.testclient import TestClient
        from src.api import app
        
        payload = {
            "action": "update",
            "type": "Project",
            "data": {}
        }
        
        with patch("src.api.sync_all_async", new_callable=AsyncMock) as mock_sync:
            mock_sync.return_value = False
            with TestClient(app) as client:
                response = client.post("/webhook/linear", json=payload)
        
        assert response.status_code == 200
        assert response.json()["status"] == "ignored"


class TestEnhancementMarkers:
    """Tests for enhancement marker encoding/decoding."""

    def test_encode_decode_roundtrip(self):
        from src.api import _encode_original_description, _decode_original_description
        
        original = "This is the original description with special chars: <>&\""
        encoded = _encode_original_description(original)
        decoded = _decode_original_description(encoded)
        
        assert decoded == original

    def test_extract_original_description(self):
        from src.api import (
            _extract_original_description,
            _build_enhancement_markers,
        )
        
        original = "My original notes"
        markers = _build_enhancement_markers(original)
        enhanced = f"Enhanced content here.\n\n{markers}"
        
        extracted = _extract_original_description(enhanced)
        
        assert extracted == original

    def test_extract_returns_none_when_no_marker(self):
        from src.api import _extract_original_description
        
        assert _extract_original_description("No markers here") is None
