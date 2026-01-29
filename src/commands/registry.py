"""Command registry - discovers and dispatches slash commands."""

from fastapi import BackgroundTasks

from src.commands.command import SlashCommand, CommandContext, CommandResult
from src.commands.handlers import AskCommand, EnhanceCommand, HelpCommand, RetryCommand


def get_all_commands() -> list[SlashCommand]:
    """Get instances of all registered commands."""
    return [
        HelpCommand(),
        AskCommand(),
        EnhanceCommand(),
        RetryCommand(),
    ]


def _get_command_map() -> dict[str, SlashCommand]:
    """Build a map of command name -> handler."""
    return {cmd.name: cmd for cmd in get_all_commands()}


async def dispatch_command(
    comment_body: str,
    issue_id: str,
    issue_identifier: str,
    user_id: str,
    user_name: str,
    background_tasks: BackgroundTasks,
    comment_id: str | None = None,
    parent_comment_id: str | None = None,
) -> CommandResult | None:
    """Parse and dispatch a slash command from a comment.
    
    Args:
        comment_body: The comment text
        issue_id: Linear issue ID
        issue_identifier: Issue identifier (e.g., "ENG-123")
        user_id: ID of user who made the comment
        user_name: Display name of user
        background_tasks: FastAPI background tasks for async work
        comment_id: ID of the comment containing the command
        parent_comment_id: ID of parent comment if this is a reply
    
    Returns:
        CommandResult if a command was found and executed, None otherwise.
    """
    stripped = comment_body.strip()
    if not stripped.startswith("/"):
        return None
    
    # Parse command name (first word after /)
    parts = stripped[1:].split(None, 1)  # Split on first whitespace
    if not parts:
        return None
    
    command_name = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""
    
    commands = _get_command_map()
    handler = commands.get(command_name)
    if not handler:
        return None
    
    ctx = CommandContext(
        issue_id=issue_id,
        issue_identifier=issue_identifier,
        args=args,
        user_id=user_id,
        user_name=user_name,
        raw_body=comment_body,
        background_tasks=background_tasks,
        comment_id=comment_id,
        parent_comment_id=parent_comment_id,
    )
    
    return await handler.execute(ctx)


def list_commands() -> list[tuple[str, str]]:
    """List all available commands with their descriptions."""
    return [(f"/{cmd.name}", cmd.description) for cmd in get_all_commands()]
