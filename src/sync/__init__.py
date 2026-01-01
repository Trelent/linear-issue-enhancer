import json
import asyncio
from pathlib import Path
from datetime import datetime

from .slack import sync_slack
from .gdrive import sync_gdrive

STATE_FILE = "sync_state.json"


class StateManager:
    """Thread-safe state manager with progressive saving."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.state_path = data_dir / STATE_FILE
        self.lock = asyncio.Lock()
        self.state = self._load()

    def _load(self) -> dict:
        if not self.state_path.exists():
            return {"last_sync": None, "slack": {}, "gdrive": {}}
        return json.loads(self.state_path.read_text())

    def _save(self):
        self.state_path.write_text(json.dumps(self.state, indent=2, default=str))

    def get(self, source: str) -> dict:
        return self.state.get(source, {})

    async def update_item(self, source: str, item_id: str, item_state: dict):
        """Update a single item's state and save to disk."""
        async with self.lock:
            if source not in self.state:
                self.state[source] = {}
            self.state[source][item_id] = item_state
            self._save()

    async def finalize(self):
        """Mark sync as complete with timestamp."""
        async with self.lock:
            self.state["last_sync"] = datetime.now().isoformat()
            self._save()


def load_state(data_dir: Path) -> dict:
    state_path = data_dir / STATE_FILE
    if not state_path.exists():
        return {"last_sync": None, "slack": {}, "gdrive": {}}
    return json.loads(state_path.read_text())


def save_state(data_dir: Path, state: dict):
    state_path = data_dir / STATE_FILE
    state_path.write_text(json.dumps(state, indent=2, default=str))


async def sync_all_async(data_dir: str, slack_token: str | None = None, gdrive_creds: str | None = None) -> bool:
    """Sync all sources sequentially for cleaner output. Returns True if new data was fetched."""
    data_path = Path(data_dir)
    data_path.mkdir(parents=True, exist_ok=True)

    state_manager = StateManager(data_path)
    updated = False

    # Run sequentially for cleaner logging
    if slack_token:
        try:
            result = await sync_slack(data_path / "slack", slack_token, state_manager.get("slack"), state_manager, "slack")
            if result:
                updated = True
        except Exception as e:
            print(f"  ⚠ Slack sync error: {e}")

    if gdrive_creds:
        try:
            result = await sync_gdrive(data_path / "gdrive", gdrive_creds, state_manager.get("gdrive"), state_manager, "gdrive")
            if result:
                updated = True
        except Exception as e:
            print(f"  ⚠ GDrive sync error: {e}")

    await state_manager.finalize()
    return updated


def sync_all(data_dir: str, slack_token: str | None = None, gdrive_creds: str | None = None) -> bool:
    """Sync all sources in parallel (sync wrapper). Returns True if new data was fetched."""
    return asyncio.run(sync_all_async(data_dir, slack_token=slack_token, gdrive_creds=gdrive_creds))


def needs_sync(data_dir: str, max_age_minutes: int = 30) -> bool:
    """Check if sync is needed based on last sync time."""
    data_path = Path(data_dir)
    state = load_state(data_path)

    last_sync = state.get("last_sync")
    if not last_sync:
        return True

    last_sync_dt = datetime.fromisoformat(last_sync)
    age = (datetime.now() - last_sync_dt).total_seconds() / 60
    return age > max_age_minutes


__all__ = ["sync_all", "sync_all_async", "needs_sync", "sync_slack", "sync_gdrive", "StateManager"]
