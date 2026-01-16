"""Base slash command interface."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import BackgroundTasks


@dataclass
class CommandContext:
    """Context passed to slash command handlers."""
    issue_id: str
    issue_identifier: str
    args: str  # Everything after the command (e.g., "/ask how does X work" -> "how does X work")
    user_id: str
    user_name: str
    raw_body: str  # Full comment body
    background_tasks: "BackgroundTasks"  # For queuing async work


@dataclass
class CommandResult:
    """Result returned from a slash command."""
    status: str  # "queued", "ignored", "error"
    action: str  # The command name
    issue_id: str
    message: str = ""
    model: str = "default"


class SlashCommand(ABC):
    """Base class for slash commands.
    
    To create a new command:
    1. Subclass SlashCommand
    2. Set `name` class attribute (e.g., "retry", "ask")
    3. Implement `execute()` 
    4. Register in registry.py
    """
    
    # Override in subclass - command name without the slash (e.g., "retry" for /retry)
    name: str = ""
    description: str = ""
    args_hint: str = ""  # e.g., "<question>" or "[feedback]" - shown in help
    
    @abstractmethod
    async def execute(self, ctx: CommandContext) -> CommandResult:
        """Execute the command.
        
        Args:
            ctx: Command context with issue info, args, user info, and background_tasks
            
        Returns:
            CommandResult indicating outcome
        """
        pass
