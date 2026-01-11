"""Tests for sync module - StateManager, needs_sync, and helpers."""

import json
import pytest
from datetime import datetime, timedelta
from pathlib import Path

from src.sync import StateManager, needs_sync, load_state, save_state


class TestStateManager:
    """Tests for the StateManager class."""

    @pytest.mark.asyncio
    async def test_init_creates_empty_state(self, temp_data_dir):
        manager = StateManager(temp_data_dir)
        
        assert manager.state == {"last_sync": None, "slack": {}, "gdrive": {}}

    @pytest.mark.asyncio
    async def test_init_loads_existing_state(self, temp_data_dir):
        existing = {"last_sync": "2024-01-01T00:00:00", "slack": {"ch1": {"last_ts": "123"}}, "gdrive": {}}
        (temp_data_dir / "sync_state.json").write_text(json.dumps(existing))
        
        manager = StateManager(temp_data_dir)
        
        assert manager.state == existing

    @pytest.mark.asyncio
    async def test_update_item_saves_to_disk(self, temp_data_dir):
        manager = StateManager(temp_data_dir)
        
        await manager.update_item("slack", "channel_123", {"last_ts": "456", "name": "general"})
        
        saved = json.loads((temp_data_dir / "sync_state.json").read_text())
        assert saved["slack"]["channel_123"] == {"last_ts": "456", "name": "general"}

    @pytest.mark.asyncio
    async def test_finalize_sets_last_sync(self, temp_data_dir):
        manager = StateManager(temp_data_dir)
        
        await manager.finalize()
        
        assert manager.state["last_sync"] is not None
        saved = json.loads((temp_data_dir / "sync_state.json").read_text())
        assert saved["last_sync"] is not None

    @pytest.mark.asyncio
    async def test_get_returns_source_state(self, temp_data_dir):
        existing = {"last_sync": None, "slack": {"ch1": {"last_ts": "123"}}, "gdrive": {}}
        (temp_data_dir / "sync_state.json").write_text(json.dumps(existing))
        
        manager = StateManager(temp_data_dir)
        
        assert manager.get("slack") == {"ch1": {"last_ts": "123"}}
        assert manager.get("gdrive") == {}
        assert manager.get("unknown") == {}


class TestNeedsSync:
    """Tests for the needs_sync function."""

    def test_needs_sync_when_no_state_file(self, temp_data_dir):
        assert needs_sync(str(temp_data_dir), max_age_minutes=30) is True

    def test_needs_sync_when_no_last_sync(self, temp_data_dir):
        state = {"last_sync": None, "slack": {}, "gdrive": {}}
        (temp_data_dir / "sync_state.json").write_text(json.dumps(state))
        
        assert needs_sync(str(temp_data_dir), max_age_minutes=30) is True

    def test_needs_sync_when_stale(self, temp_data_dir):
        old_time = (datetime.now() - timedelta(hours=1)).isoformat()
        state = {"last_sync": old_time, "slack": {}, "gdrive": {}}
        (temp_data_dir / "sync_state.json").write_text(json.dumps(state))
        
        assert needs_sync(str(temp_data_dir), max_age_minutes=30) is True

    def test_no_sync_needed_when_fresh(self, temp_data_dir):
        recent_time = (datetime.now() - timedelta(minutes=5)).isoformat()
        state = {"last_sync": recent_time, "slack": {}, "gdrive": {}}
        (temp_data_dir / "sync_state.json").write_text(json.dumps(state))
        
        assert needs_sync(str(temp_data_dir), max_age_minutes=30) is False


class TestLoadSaveState:
    """Tests for load_state and save_state helpers."""

    def test_load_state_returns_default_when_missing(self, temp_data_dir):
        state = load_state(temp_data_dir)
        
        assert state == {"last_sync": None, "slack": {}, "gdrive": {}}

    def test_save_and_load_roundtrip(self, temp_data_dir):
        original = {"last_sync": "2024-01-01T00:00:00", "slack": {"x": 1}, "gdrive": {"y": 2}}
        
        save_state(temp_data_dir, original)
        loaded = load_state(temp_data_dir)
        
        assert loaded == original
