"""Handler for /help command."""

from src.linear import add_comment
from src.commands.command import SlashCommand, CommandContext, CommandResult
from src.commands.threading import get_reply_target


class HelpCommand(SlashCommand):
    """List all available slash commands."""
    
    name = "help"
    description = "Show this help message (replies in thread)"
    args_hint = ""
    
    async def execute(self, ctx: CommandContext) -> CommandResult:
        # Import here to avoid circular imports
        from src.commands.registry import get_all_commands
        
        commands = get_all_commands()
        reply_to_id = get_reply_target(ctx.comment_id, ctx.parent_comment_id)
        
        lines = ["## Available Commands\n"]
        for cmd in commands:
            usage = f"/{cmd.name}"
            if cmd.args_hint:
                usage += f" {cmd.args_hint}"
            lines.append(f"**`{usage}`**")
            lines.append(f"{cmd.description}\n")
        
        lines.append("---")
        lines.append("_Use `[model=X]` to specify a model (e.g., `opus`, `sonnet`)_")
        
        help_text = "\n".join(lines)
        
        print(f"", flush=True)
        print(f"▶ [WH] HELP COMMAND for issue {ctx.issue_id}", flush=True)
        if reply_to_id:
            print(f"       Reply to: {reply_to_id}{' (parent)' if ctx.parent_comment_id else ''}", flush=True)
        
        await add_comment(ctx.issue_id, help_text, parent_id=reply_to_id)
        
        return CommandResult(
            status="completed",
            action=self.name,
            issue_id=ctx.issue_id,
        )
