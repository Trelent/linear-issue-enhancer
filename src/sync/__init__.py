"""Data sync module - fetches and caches data from various sources."""

import json
import asyncio
from pathlib import Path
from datetime import datetime

from .connector import Connector, ConnectorResult
from .registry import get_all_connectors, get_enabled_connectors

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
            return {"last_sync": None}
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
        return {"last_sync": None}
    return json.loads(state_path.read_text())


def save_state(data_dir: Path, state: dict):
    state_path = data_dir / STATE_FILE
    state_path.write_text(json.dumps(state, indent=2, default=str))


async def sync_all_async(data_dir: str, connector_filter: list[str] | None = None) -> bool:
    """Sync all enabled data sources. Returns True if new data was fetched.
    
    Args:
        data_dir: Directory to store synced data
        connector_filter: Optional list of connector names to sync (e.g., ['gmail', 'slack']).
                         If None, syncs all enabled connectors.
    """
    data_path = Path(data_dir)
    data_path.mkdir(parents=True, exist_ok=True)
    
    state_manager = StateManager(data_path)
    connectors = get_enabled_connectors()
    
    if connector_filter:
        connectors = [c for c in connectors if c.name in connector_filter]
        if not connectors:
            print(f"  ⚠ No enabled connectors match filter: {', '.join(connector_filter)}")
            return False
    
    if not connectors:
        print("  ⚠ No connectors enabled")
        return False
    
    updated = False
    
    for connector in connectors:
        # Run setup to validate config and test connection
        if not connector.setup():
            print(f"  ⚠ {connector.name}: Setup failed, skipping")
            continue
        
        # Get output directory for this connector
        output_dir = data_path / connector.name
        
        try:
            new_state, result = await connector.download(
                output_dir=output_dir,
                state=state_manager.get(connector.name),
                state_manager=state_manager,
            )
            
            if result.items_synced > 0:
                updated = True
                
        except Exception as e:
            print(f"  ⚠ {connector.name}: Sync error - {e}")
    
    await state_manager.finalize()
    return updated


def sync_all(data_dir: str, connector_filter: list[str] | None = None) -> bool:
    """Sync all enabled sources (sync wrapper). Returns True if new data was fetched.
    
    Args:
        data_dir: Directory to store synced data
        connector_filter: Optional list of connector names to sync (e.g., ['gmail', 'slack']).
                         If None, syncs all enabled connectors.
    """
    return asyncio.run(sync_all_async(data_dir, connector_filter=connector_filter))


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


def print_connector_status():
    """Print status of all connectors."""
    print("Connector Status:")
    for connector in get_all_connectors():
        status = "✓ enabled" if connector.enabled else "✗ disabled"
        env_hint = f" ({connector.env_key})" if not connector.enabled else ""
        print(f"  {connector.name}: {status}{env_hint}")


__all__ = [
    "sync_all",
    "sync_all_async",
    "needs_sync",
    "print_connector_status",
    "StateManager",
    "Connector",
    "ConnectorResult",
    "get_enabled_connectors",
    "get_all_connectors",
]
