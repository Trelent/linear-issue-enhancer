"""Base connector interface for data sync sources."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.sync import StateManager


@dataclass
class ConnectorResult:
    """Result of a connector sync operation."""
    success: bool
    items_synced: int = 0
    items_skipped: int = 0
    message: str = ""


class Connector(ABC):
    """Base class for all data sync connectors.
    
    To create a new connector:
    1. Subclass Connector
    2. Set `name` and `env_key` class attributes
    3. Implement `setup()` and `download()`
    4. Register in CONNECTORS list in registry.py
    """
    
    # Override in subclass
    name: str = "base"
    env_key: str = ""  # e.g. "SLACK_TOKEN" - if set and non-empty, connector is enabled
    
    def __init__(self):
        self._config: dict = {}
    
    @property
    def enabled(self) -> bool:
        """Check if connector is enabled via environment."""
        import os
        if not self.env_key:
            return False
        return bool(os.getenv(self.env_key))
    
    @abstractmethod
    def setup(self) -> bool:
        """Load config from environment and validate.
        
        Returns True if setup succeeded and connector is ready.
        Should print status messages (✓ for success, ✗ for failure).
        """
        pass
    
    @abstractmethod
    async def download(
        self,
        output_dir: Path,
        state: dict,
        state_manager: "StateManager | None" = None,
    ) -> tuple[dict, ConnectorResult]:
        """Download/sync data from the source.
        
        Args:
            output_dir: Directory to write output files
            state: Previous state dict for this connector (for incremental sync)
            state_manager: Optional manager for progressive state saves
            
        Returns:
            Tuple of (new_state_dict, ConnectorResult)
        """
        pass
