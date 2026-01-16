"""Handler for /ask command."""

from src.agents import parse_model_tag
from src.commands.command import SlashCommand, CommandContext, CommandResult
from src.commands.tasks import answer_question


class AskCommand(SlashCommand):
    """Answer a question using context and code research."""
    
    name = "ask"
    description = "Ask a question and get an AI-researched answer"
    args_hint = "<question> [model=X]"
    
    async def execute(self, ctx: CommandContext) -> CommandResult:
        question = ctx.args
        
        if not question:
            print(f"  → /ask command with no question, ignored", flush=True)
            return CommandResult(
                status="ignored",
                action=self.name,
                issue_id=ctx.issue_id,
                message="No question provided",
            )
        
        model_shorthand = parse_model_tag(question)
        
        print(f"", flush=True)
        print(f"▶ [WH] ASK COMMAND for issue {ctx.issue_id}", flush=True)
        print(f"       Model: {model_shorthand or 'default'}", flush=True)
        print(f"       User: {ctx.user_name}", flush=True)
        print(f"       Question: {question[:60]}{'...' if len(question) > 60 else ''}", flush=True)
        
        ctx.background_tasks.add_task(answer_question, ctx.issue_id, question, ctx.user_name, model_shorthand)
        
        return CommandResult(
            status="queued",
            action=self.name,
            issue_id=ctx.issue_id,
            model=model_shorthand or "default",
        )
