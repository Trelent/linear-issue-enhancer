"""Background task for /enhance command."""

from src.linear import get_issue, add_comment
from src.commands.shared import ENHANCEMENT_MARKER


async def run_enhance_issue(
    issue_id: str,
    model_shorthand: str | None = None,
    reply_to_id: str | None = None,
):
    """Run enhancement for an issue triggered by /enhance command."""
    # Import here to avoid circular imports
    from src.api import enhance_issue
    
    # Fetch current issue data
    try:
        issue = await get_issue(issue_id)
    except Exception as e:
        await add_comment(
            issue_id,
            f"❌ _Could not fetch issue data: {e}_",
            parent_id=reply_to_id,
        )
        return
    
    description = issue.description or ""
    
    # Check if already enhanced
    if ENHANCEMENT_MARKER in description:
        await add_comment(
            issue_id,
            "ℹ️ _This issue has already been enhanced. Use `/retry` to re-enhance with feedback._",
            parent_id=reply_to_id,
        )
        return
    
    # Run enhancement (team is available, but project requires separate query)
    await enhance_issue(
        issue_id=issue_id,
        title=issue.title,
        existing_description=description,
        project_name=None,  # Not fetched in get_issue
        team_name=issue.team_name,
        model_shorthand=model_shorthand,
    )
