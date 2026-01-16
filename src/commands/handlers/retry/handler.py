"""Handler for /retry command."""

from src.agents import parse_model_tag
from src.commands.command import SlashCommand, CommandContext, CommandResult
from src.commands.threading import get_reply_target
from .task import retry_enhance_issue


class RetryCommand(SlashCommand):
    """Re-enhance an issue with optional feedback."""
    
    name = "retry"
    description = "Re-run issue enhancement with optional feedback (replies in thread)"
    args_hint = "[feedback] [model=X]"
    
    async def execute(self, ctx: CommandContext) -> CommandResult:
        feedback = ctx.args
        model_shorthand = parse_model_tag(feedback)
        reply_to_id = get_reply_target(ctx.comment_id, ctx.parent_comment_id)
        
        print(f"", flush=True)
        print(f"▶ [WH] RETRY REQUESTED for issue {ctx.issue_id}", flush=True)
        print(f"       Model: {model_shorthand or 'default'}", flush=True)
        if feedback:
            print(f"       Feedback: {feedback[:60]}...", flush=True)
        if reply_to_id:
            print(f"       Reply to: {reply_to_id}{' (parent)' if ctx.parent_comment_id else ''}", flush=True)
        
        ctx.background_tasks.add_task(
            retry_enhance_issue,
            ctx.issue_id,
            feedback,
            model_shorthand,
            reply_to_id,
        )
        
        return CommandResult(
            status="queued",
            action=self.name,
            issue_id=ctx.issue_id,
            model=model_shorthand or "default",
        )
