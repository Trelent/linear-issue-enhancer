import json
from pathlib import Path
from datetime import datetime

from .slack import sync_slack
from .gdrive import sync_gdrive

STATE_FILE = "sync_state.json"


def load_state(data_dir: Path) -> dict:
    state_path = data_dir / STATE_FILE
    if not state_path.exists():
        return {"last_sync": None, "slack": {}, "gdrive": {}}
    return json.loads(state_path.read_text())


def save_state(data_dir: Path, state: dict):
    state_path = data_dir / STATE_FILE
    state_path.write_text(json.dumps(state, indent=2, default=str))


def sync_all(data_dir: str, slack_token: str | None = None, gdrive_creds: str | None = None) -> bool:
    """Sync all sources. Returns True if new data was fetched."""
    data_path = Path(data_dir)
    data_path.mkdir(parents=True, exist_ok=True)

    state = load_state(data_path)
    updated = False

    if slack_token:
        slack_updated = sync_slack(data_path / "slack", slack_token, state.get("slack", {}))
        state["slack"] = slack_updated
        updated = updated or bool(slack_updated)

    if gdrive_creds:
        gdrive_updated = sync_gdrive(data_path / "gdrive", gdrive_creds, state.get("gdrive", {}))
        state["gdrive"] = gdrive_updated
        updated = updated or bool(gdrive_updated)

    state["last_sync"] = datetime.now().isoformat()
    save_state(data_path, state)

    return updated


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


__all__ = ["sync_all", "needs_sync", "sync_slack", "sync_gdrive"]

