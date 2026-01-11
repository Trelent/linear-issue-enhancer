"""Connector registry - discovers and manages all sync connectors."""

from .connector import Connector


def get_all_connectors() -> list[Connector]:
    """Get instances of all registered connectors."""
    # Import here to avoid circular imports
    from .connectors.slack import SlackConnector
    from .connectors.gdrive import GDriveConnector
    from .connectors.github import GitHubConnector
    from .connectors.gmail import GmailConnector
    
    return [
        SlackConnector(),
        GDriveConnector(),
        GitHubConnector(),
        GmailConnector(),
    ]


def get_enabled_connectors() -> list[Connector]:
    """Get only connectors that are enabled via environment."""
    return [c for c in get_all_connectors() if c.enabled]
