"""Background tasks for slash commands."""

import os
import tempfile

from agents import Runner

from src.agents import (
    create_context_researcher,
    create_code_researcher,
    create_issue_writer,
    create_question_answerer,
)
from src.linear import add_comment, get_issue, get_issue_comments, update_issue_description
from src.sync import sync_all_async
from src.tools import set_repos_base_dir, clear_cloned_repos


MAX_TURNS = 250
DOCS_DIR = os.getenv("DOCS_DIR", "./data")

# Enhancement markers
ENHANCEMENT_MARKER = "<!-- enhanced-by-linear-enhancer -->"
ORIGINAL_DESC_MARKER_START = "<!-- original-description:"
ORIGINAL_DESC_MARKER_END = ":end-original -->"


def _encode_original_description(original: str) -> str:
    """Encode original description for storage in marker."""
    import base64
    return base64.b64encode(original.encode()).decode()


def _decode_original_description(encoded: str) -> str:
    """Decode original description from marker."""
    import base64
    return base64.b64decode(encoded.encode()).decode()


def _extract_original_description(description: str) -> str | None:
    """Extract original description from an enhanced description."""
    start_idx = description.find(ORIGINAL_DESC_MARKER_START)
    if start_idx == -1:
        return None
    
    end_idx = description.find(ORIGINAL_DESC_MARKER_END, start_idx)
    if end_idx == -1:
        return None
    
    encoded = description[start_idx + len(ORIGINAL_DESC_MARKER_START):end_idx].strip()
    return _decode_original_description(encoded)


def _build_enhancement_markers(original_description: str) -> str:
    """Build the markers to append to enhanced descriptions."""
    encoded = _encode_original_description(original_description)
    return f"{ENHANCEMENT_MARKER}\n{ORIGINAL_DESC_MARKER_START} {encoded} {ORIGINAL_DESC_MARKER_END}"


async def _research_context(prompt: str, model_shorthand: str | None = None) -> str:
    """Research context from Slack/GDrive."""
    agent = create_context_researcher(model_shorthand)
    result = await Runner.run(
        agent,
        f"Find all context relevant to this issue:\n\n{prompt}\n\nSearch in: {DOCS_DIR}",
        max_turns=MAX_TURNS,
    )
    return str(result.final_output)


async def _research_codebase(prompt: str, context: str, work_dir: str, model_shorthand: str | None = None) -> str:
    """Research the codebase, informed by context from Slack/GDrive."""
    repos_dir = os.path.join(work_dir, "repos")
    clear_cloned_repos()
    set_repos_base_dir(repos_dir)
    
    agent = create_code_researcher(model_shorthand)
    result = await Runner.run(
        agent,
        f"""Analyze the codebase for the following issue:

## Issue
{prompt}

## Context from Slack/GDrive
{context}

## Instructions
1. **Discover repos**: Use `list_github_repos` to see available repositories
2. **Identify ALL relevant repos**: Based on the issue and context, there may be multiple repos involved
   (e.g., frontend + backend, shared libs, infrastructure)
3. **Check for relevant PRs**: Use `list_prs` on each relevant repo
4. **Determine the right branch** for each repo:
   - If context mentions a specific branch (e.g. "on dev", "in feature-x"), use `list_repo_branches` to find it
   - If a PR is relevant, use `get_pr_details` to inspect it and consider cloning its branch
   - Otherwise, use the repo's default branch
5. **Clone ALL relevant repos**: Each clone goes to a unique directory automatically
6. **Use `list_cloned_repos`** to see all cloned repos and their paths
7. **Cross-reference**: Search for relevant code across all cloned repos

Pay attention to any branch names, PR references, or environment mentions in the context above.
Find all relevant code, files, and implementation details.

**IMPORTANT**: If this issue involves multiple repositories (frontend/backend, shared libs, etc.), 
clone and analyze ALL of them. Use `list_cloned_repos` to track what you've cloned.""",
        max_turns=MAX_TURNS,
    )
    return str(result.final_output)


async def _write_enhanced_description(
    title: str,
    existing: str,
    context: str,
    code_analysis: str,
    model_shorthand: str | None = None,
) -> str:
    """Generate an enhanced issue description."""
    agent = create_issue_writer(model_shorthand)
    result = await Runner.run(
        agent,
        f"""Write an enhanced Linear issue description based on:

## Issue Title
{title}

## Original Notes
{existing or "_No original description_"}

## Context from Slack/GDrive/Documents
{context}

## Codebase Analysis
{code_analysis}

---

Write a clear issue description. Include:
- Problem statement: what needs to be done
- Context: relevant background from the research above
- Technical details: file paths, code references, error messages
- References: ONLY real URLs found in the research (PRs, docs, etc.)

IMPORTANT:
- Do NOT suggest how to implement or approach the solution
- Do NOT include a "Suggested Approach" or "Implementation" section
- Do NOT make up URLs - only include links found in the research
- Do NOT include acceptance criteria unless explicitly stated in context
- Just DESCRIBE the problem, don't PLAN the solution

Format: Markdown. No title needed - just the description body.""",
        max_turns=MAX_TURNS,
    )
    return str(result.final_output)


async def _write_retry_description(
    title: str,
    original: str,
    previous_ai_version: str,
    feedback: str,
    context: str,
    code_analysis: str,
    model_shorthand: str | None = None,
) -> str:
    """Generate a new description based on user feedback about previous attempt."""
    agent = create_issue_writer(model_shorthand)
    result = await Runner.run(
        agent,
        f"""Rewrite an enhanced Linear issue description based on user feedback:

## Issue Title
{title}

## Original Notes (from ticket creator)
{original or "_No original description_"}

## Previous AI-Generated Description
{previous_ai_version}

## User Feedback on Previous Version
{feedback or "_No specific feedback - please try again with fresh perspective_"}

## Context from Slack/GDrive/Documents
{context}

## Codebase Analysis
{code_analysis}

---

The user has requested a retry with the feedback above. Write an IMPROVED issue description that:
- Addresses their feedback/concerns
- Incorporates any additional details they mentioned
- Keeps the good parts from the previous version
- Fixes any issues they pointed out

Include:
- Problem statement: what needs to be done
- Context: relevant background from the research above
- Technical details: file paths, code references, error messages
- References: ONLY real URLs found in the research (PRs, docs, etc.)

IMPORTANT:
- Do NOT suggest how to implement or approach the solution
- Do NOT include a "Suggested Approach" or "Implementation" section
- Do NOT make up URLs - only include links found in the research
- Do NOT include acceptance criteria unless explicitly stated in context
- Just DESCRIBE the problem, don't PLAN the solution

Format: Markdown. No title needed - just the description body.""",
        max_turns=MAX_TURNS,
    )
    return str(result.final_output)


async def retry_enhance_issue(issue_id: str, feedback: str, model_shorthand: str | None = None):
    """Retry enhancing an issue based on user feedback."""
    print(f"\n{'='*60}", flush=True)
    print(f"🔄 Retrying enhancement for issue: {issue_id}", flush=True)
    print(f"   Model: {model_shorthand or 'default'}", flush=True)
    print(f"{'='*60}\n", flush=True)
    
    # Add "working on it" comment immediately
    try:
        await add_comment(issue_id, "🔄 _Retrying enhancement with your feedback..._")
    except Exception as e:
        if "Entity not found" in str(e) or "not found" in str(e).lower():
            print(f"⚠️ Issue {issue_id} no longer exists, skipping retry", flush=True)
            return
        raise
    
    # Fetch current issue data
    try:
        issue = await get_issue(issue_id)
    except Exception as e:
        print(f"❌ Failed to fetch issue: {e}", flush=True)
        await add_comment(issue_id, "❌ _Failed to fetch issue data. Please check server logs for details._")
        return
    
    current_description = issue.description or ""
    title = issue.title
    
    # Extract original description from marker
    original_description = _extract_original_description(current_description)
    if original_description is None:
        print("⚠️ No original description marker found, treating as first enhancement", flush=True)
        original_description = ""
    
    # Strip markers from current description to get the AI-written part
    ai_description = current_description
    if ENHANCEMENT_MARKER in ai_description:
        ai_description = ai_description.split(ENHANCEMENT_MARKER)[0].strip()
    
    print(f"   Title: {title}", flush=True)
    print(f"   Original: {len(original_description)} chars", flush=True)
    print(f"   AI version: {len(ai_description)} chars", flush=True)
    print(f"   Feedback: {feedback[:100]}..." if len(feedback) > 100 else f"   Feedback: {feedback}", flush=True)
    
    try:
        prompt = f"Issue: {title}"
        if original_description:
            prompt += f"\n\nOriginal notes:\n{original_description}"
        
        print("📥 Syncing data sources...", flush=True)
        await sync_all_async(DOCS_DIR)
        
        print("🔬 Step 1: Researching context (Slack/GDrive)...", flush=True)
        try:
            context = await _research_context(prompt, model_shorthand)
        except Exception as e:
            print(f"⚠️ Context research error: {e}", flush=True)
            context = f"Error researching context: {e}"
        
        print("🔬 Step 2: Researching codebase (with context)...", flush=True)
        with tempfile.TemporaryDirectory() as work_dir:
            try:
                code_analysis = await _research_codebase(prompt, context, work_dir, model_shorthand)
            except Exception as e:
                print(f"⚠️ Code research error: {e}", flush=True)
                code_analysis = f"Error researching code: {e}"
        
        print("✍️ Writing enhanced description (with feedback)...", flush=True)
        enhanced = await _write_retry_description(
            title, original_description, ai_description, feedback, context, code_analysis, model_shorthand
        )
        
        markers = _build_enhancement_markers(original_description)
        enhanced_with_marker = f"{enhanced}\n\n{markers}"
        
        print(f"📝 Updating Linear issue...", flush=True)
        success = await update_issue_description(issue_id, enhanced_with_marker)
        
        if success:
            print(f"✅ Issue re-enhanced successfully!", flush=True)
            await add_comment(issue_id, "_✅ Issue re-enhanced based on your feedback._")
        else:
            print(f"❌ Failed to update issue via Linear API", flush=True)
            await add_comment(issue_id, "⚠️ _Failed to update issue description. Please check the logs._")
            
    except Exception as e:
        print(f"❌ Retry enhancement failed with error: {e}", flush=True)
        import traceback
        traceback.print_exc()
        await add_comment(issue_id, "❌ _Retry enhancement failed during issue processing. Please check server logs for details._")


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
