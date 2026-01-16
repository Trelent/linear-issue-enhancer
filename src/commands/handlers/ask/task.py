"""Background task for /ask command."""

import os
import tempfile

from agents import Runner

from src.agents import create_question_answerer
from src.linear import add_comment, get_issue, get_issue_comments
from src.sync import sync_all_async
from src.tools import set_repos_base_dir, clear_cloned_repos
from src.commands.shared import MAX_TURNS, DOCS_DIR


async def answer_question(
    issue_id: str,
    question: str,
    user_name: str,
    model_shorthand: str | None = None,
    reply_to_id: str | None = None,
):
    """Answer a user's question using context and code research.
    
    Args:
        issue_id: The issue ID
        question: The user's question
        user_name: Display name of user who asked
        model_shorthand: Optional model selection
        reply_to_id: Optional comment ID to reply to (for threading)
    """
    print(f"\n{'='*60}", flush=True)
    print(f"❓ Answering question for issue: {issue_id}", flush=True)
    print(f"   Model: {model_shorthand or 'default'}", flush=True)
    print(f"   User: {user_name}", flush=True)
    if reply_to_id:
        print(f"   Reply to: {reply_to_id}", flush=True)
    print(f"{'='*60}\n", flush=True)
    
    # Post "thinking" message as a reply if we have a parent
    try:
        await add_comment(issue_id, "🤔 _Researching your question..._", parent_id=reply_to_id)
    except Exception as e:
        if "Entity not found" in str(e) or "not found" in str(e).lower():
            print(f"⚠️ Issue {issue_id} no longer exists, skipping answer", flush=True)
            return
        raise
    
    try:
        issue = await get_issue(issue_id)
        comments = await get_issue_comments(issue_id)
    except Exception as e:
        print(f"❌ Failed to fetch issue/comments: {e}", flush=True)
        await add_comment(issue_id, "❌ _Failed to fetch issue data. Please check server logs for details._", parent_id=reply_to_id)
        return
    
    comment_context = "\n\n".join([
        f"**{c.user_name}** ({c.created_at}):\n{c.body}"
        for c in comments
    ])
    
    try:
        print("📥 Syncing data sources...", flush=True)
        await sync_all_async(DOCS_DIR)
        
        issue_context = f"""## Issue: {issue.title}

**Identifier:** {issue.identifier}
**Status:** {issue.state_name}
**Team:** {issue.team_name}

### Description
{issue.description or "_No description_"}

### Comment History
{comment_context or "_No previous comments_"}
"""
        
        print("🔬 Researching and answering question...", flush=True)
        with tempfile.TemporaryDirectory() as work_dir:
            repos_dir = os.path.join(work_dir, "repos")
            clear_cloned_repos()
            set_repos_base_dir(repos_dir)
            
            agent = create_question_answerer(model_shorthand)
            result = await Runner.run(
                agent,
                f"""Answer the following question about this issue:

{issue_context}

---

**User's Question from {user_name}:**
{question}

---

**Instructions:**
1. Research using available tools (docs in {DOCS_DIR}, and GitHub repos)
2. Provide a clear, direct answer to the question
3. Include specific references where helpful
4. Keep your response focused and conversational""",
                max_turns=MAX_TURNS,
            )
            answer = str(result.final_output)
        
        user_tag = f"@{user_name}" if user_name else ""
        response = f"{user_tag}\n\n{answer}" if user_tag else answer
        
        print(f"📝 Posting answer{' (as reply)' if reply_to_id else ''}...", flush=True)
        success = await add_comment(issue_id, response, parent_id=reply_to_id)
        
        if success:
            print(f"✅ Question answered successfully!", flush=True)
        else:
            print(f"❌ Failed to post answer", flush=True)
            
    except Exception as e:
        print(f"❌ Answer failed with error: {e}", flush=True)
        import traceback
        traceback.print_exc()
        await add_comment(issue_id, "❌ _Failed to answer question. Please check server logs for details._", parent_id=reply_to_id)
