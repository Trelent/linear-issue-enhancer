"""Handler for /enhance command."""

from src.agents import parse_model_tag
from src.commands.command import SlashCommand, CommandContext, CommandResult
from src.commands.threading import get_reply_target
from .task import run_enhance_issue


class EnhanceCommand(SlashCommand):
    """Manually trigger issue enhancement."""
    
    name = "enhance"
    description = "Enhance this issue with AI-researched context"
    args_hint = "[model=X]"
    
    async def execute(self, ctx: CommandContext) -> CommandResult:
        model_shorthand = parse_model_tag(ctx.args)
        reply_to_id = get_reply_target(ctx.comment_id, ctx.parent_comment_id)
        
        print(f"", flush=True)
        print(f"▶ [WH] ENHANCE REQUESTED for issue {ctx.issue_id}", flush=True)
        print(f"       Model: {model_shorthand or 'default'}", flush=True)
        if reply_to_id:
            print(f"       Reply to: {reply_to_id}{' (parent)' if ctx.parent_comment_id else ''}", flush=True)
        
        ctx.background_tasks.add_task(
            run_enhance_issue,
            ctx.issue_id,
            model_shorthand,
            reply_to_id,
        )
        
        return CommandResult(
            status="queued",
            action=self.name,
            issue_id=ctx.issue_id,
            model=model_shorthand or "default",
        )
