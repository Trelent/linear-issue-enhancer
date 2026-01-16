"""FastAPI webhook server for Linear integration."""

import asyncio
import hashlib
import hmac
import os
import tempfile
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from dotenv import load_dotenv
from fastapi import FastAPI, Request, BackgroundTasks, HTTPException

load_dotenv(override=True)

# Set up tracing before importing agents
from agents.tracing import add_trace_processor
from src.tracing import ConsoleTracer
add_trace_processor(ConsoleTracer())

from src.linear import update_issue_description, add_comment, get_issue, get_issue_comments
from src.agents import (
    create_context_researcher,
    create_code_researcher,
    create_issue_writer,
    create_question_answerer,
    parse_model_tag,
)
from src.sync import sync_all_async, print_connector_status
from src.tools import set_repos_base_dir, clear_cloned_repos
from agents import Runner


MAX_TURNS = 250
DOCS_DIR = os.getenv("DOCS_DIR", "./data")
LINEAR_WEBHOOK_SECRET = os.getenv("LINEAR_WEBHOOK_SECRET")
SYNC_INTERVAL_HOURS = int(os.getenv("SYNC_INTERVAL_HOURS", "1"))

# Comma-separated list of Linear project names to exclude from enhancement
_excluded_projects_raw = os.getenv("LINEAR_EXCLUDED_PROJECTS", "")
LINEAR_EXCLUDED_PROJECTS = {p.strip().lower() for p in _excluded_projects_raw.split(",") if p.strip()}

# Track recently processed issues to prevent infinite loops
# Key: issue_id, Value: timestamp
_recently_processed: dict[str, float] = {}
PROCESS_COOLDOWN_SECONDS = 300  # 5 minutes

# Marker we add to enhanced descriptions (includes original description for retry)
ENHANCEMENT_MARKER = "<!-- enhanced-by-linear-enhancer -->"
ORIGINAL_DESC_MARKER_START = "<!-- original-description:"
ORIGINAL_DESC_MARKER_END = ":end-original -->"

# Scheduler instance
scheduler = AsyncIOScheduler()


async def scheduled_sync():
    """Run periodic sync of data sources."""
    print(f"\n{'='*60}", flush=True)
    print("⏰ Scheduled sync starting...", flush=True)
    print(f"{'='*60}\n", flush=True)
    try:
        await sync_all_async(DOCS_DIR)
        print("✅ Scheduled sync complete!", flush=True)
    except Exception as e:
        print(f"❌ Scheduled sync failed: {e}", flush=True)
        import traceback
        traceback.print_exc()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """App lifespan handler."""
    print("🚀 Linear Enhancer API starting...", flush=True)
    print_connector_status()
    if LINEAR_EXCLUDED_PROJECTS:
        print(f"   Excluded projects: {', '.join(sorted(LINEAR_EXCLUDED_PROJECTS))}", flush=True)
    
    # Run initial sync on boot
    print("📥 Running initial sync on boot...", flush=True)
    try:
        await sync_all_async(DOCS_DIR)
        print("✅ Initial sync complete!", flush=True)
    except Exception as e:
        print(f"⚠️ Initial sync failed: {e}", flush=True)
    
    # Start the scheduler for periodic syncs
    scheduler.add_job(
        scheduled_sync,
        trigger=IntervalTrigger(hours=SYNC_INTERVAL_HOURS),
        id="periodic_sync",
        name=f"Sync every {SYNC_INTERVAL_HOURS} hours",
        replace_existing=True,
    )
    scheduler.start()
    print(f"⏰ Scheduler started: sync every {SYNC_INTERVAL_HOURS} hours", flush=True)
    
    yield
    
    # Shutdown scheduler
    scheduler.shutdown()
    print("👋 Shutting down...", flush=True)


app = FastAPI(
    title="Linear Enhancer",
    description="AI-powered issue enhancement from Slack, GDrive, and GitHub context",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}


def _verify_signature(body: bytes, signature: str | None) -> bool:
    """Verify Linear webhook signature."""
    if not LINEAR_WEBHOOK_SECRET:
        return True  # Skip verification if no secret configured
    if not signature:
        return False
    expected = hmac.new(
        LINEAR_WEBHOOK_SECRET.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def _was_recently_processed(issue_id: str) -> bool:
    """Check if we recently processed this issue."""
    import time
    now = time.time()
    
    # Clean up old entries
    expired = [k for k, v in _recently_processed.items() if now - v > PROCESS_COOLDOWN_SECONDS]
    for k in expired:
        del _recently_processed[k]
    
    return issue_id in _recently_processed


def _mark_as_processed(issue_id: str):
    """Mark an issue as recently processed."""
    import time
    _recently_processed[issue_id] = time.time()


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


@app.post("/webhook/linear")
async def linear_webhook(request: Request, background_tasks: BackgroundTasks):
    """Handle Linear webhook events."""
    body = await request.body()
    signature = request.headers.get("linear-signature")
    
    if not _verify_signature(body, signature):
        print("❌ [WH] Signature verification failed", flush=True)
        raise HTTPException(status_code=401, detail="Invalid signature")
    
    payload = await request.json()
    
    action = payload.get("action")
    event_type = payload.get("type")
    data = payload.get("data", {})
    
    # Route to appropriate handler
    if event_type == "Comment" and action == "create":
        return await _handle_comment_create(data, background_tasks)
    
    if event_type == "Issue" and action == "create":
        return await _handle_issue_create(data, background_tasks)
    
    print(f"· [WH] {event_type}/{action} → ignored", flush=True)
    return {"status": "ignored", "reason": f"Unhandled event: {event_type}/{action}"}


async def _handle_comment_create(data: dict, background_tasks: BackgroundTasks):
    """Handle comment creation - check for slash commands (/retry, /ask)."""
    comment_body = data.get("body") or ""
    issue_data = data.get("issue", {})
    issue_id = issue_data.get("id")
    issue_identifier = issue_data.get("identifier", "?")
    user_data = data.get("user", {})
    user_name = user_data.get("displayName", "")
    
    print(f"· [WH] Comment/create on {issue_identifier}: \"{comment_body[:50]}{'...' if len(comment_body) > 50 else ''}\"", flush=True)
    
    if not issue_id:
        print(f"  → Missing issue ID in payload, ignored", flush=True)
        return {"status": "ignored", "reason": "Missing issue ID in comment"}
    
    comment_stripped = comment_body.strip()
    
    # Check for /ask command
    if comment_stripped.startswith("/ask"):
        return await _handle_ask_command(issue_id, comment_stripped, user_name, background_tasks)
    
    # Check for /retry command
    if comment_stripped.startswith("/retry"):
        return await _handle_retry_command(issue_id, comment_stripped, background_tasks)
    
    print(f"  → Not a slash command, ignored", flush=True)
    return {"status": "ignored", "reason": "Not a slash command"}


async def _handle_retry_command(issue_id: str, comment_body: str, background_tasks: BackgroundTasks):
    """Handle /retry command."""
    # Extract feedback after /retry
    feedback = comment_body[6:].strip()  # Remove "/retry" prefix
    
    # Parse model selection from comment (e.g., "/retry [model=opus] please try again")
    model_shorthand = parse_model_tag(feedback)
    
    print(f"", flush=True)
    print(f"▶ [WH] RETRY REQUESTED for issue {issue_id}", flush=True)
    print(f"       Model: {model_shorthand or 'default'}", flush=True)
    if feedback:
        print(f"       Feedback: {feedback[:60]}...", flush=True)
    
    # Mark as processing to prevent loops
    _mark_as_processed(issue_id)
    
    # Queue retry in background
    background_tasks.add_task(retry_enhance_issue, issue_id, feedback, model_shorthand)
    
    return {"status": "queued", "action": "retry", "issue_id": issue_id, "model": model_shorthand or "default"}


async def _handle_ask_command(issue_id: str, comment_body: str, user_name: str, background_tasks: BackgroundTasks):
    """Handle /ask command."""
    # Extract question after /ask
    question = comment_body[4:].strip()  # Remove "/ask" prefix
    
    if not question:
        print(f"  → /ask command with no question, ignored", flush=True)
        return {"status": "ignored", "reason": "No question provided"}
    
    # Parse model selection from comment (e.g., "/ask [model=opus] what is X?")
    model_shorthand = parse_model_tag(question)
    
    print(f"", flush=True)
    print(f"▶ [WH] ASK COMMAND for issue {issue_id}", flush=True)
    print(f"       Model: {model_shorthand or 'default'}", flush=True)
    print(f"       User: {user_name}", flush=True)
    print(f"       Question: {question[:60]}{'...' if len(question) > 60 else ''}", flush=True)
    
    # Queue answer in background
    background_tasks.add_task(answer_question, issue_id, question, user_name, model_shorthand)
    
    return {"status": "queued", "action": "ask", "issue_id": issue_id, "model": model_shorthand or "default"}


async def _handle_issue_create(data: dict, background_tasks: BackgroundTasks):
    """Handle issue creation - enhance new issues."""
    issue_id = data.get("id", "?")
    title = data.get("title", "?")
    
    if not data.get("id"):
        print(f"· [WH] Issue/create but missing ID → error", flush=True)
        raise HTTPException(status_code=400, detail="Missing issue ID")
    
    description = data.get("description") or ""
    desc_len = len(description)
    
    # Extract project/team context
    project = data.get("project", {})
    project_name = project.get("name") if project else None
    team = data.get("team", {})
    team_name = team.get("name") if team else None
    
    # Check if we already processed this issue recently (prevents loops)
    if _was_recently_processed(issue_id):
        print(f"· [WH] Issue/create \"{title[:40]}\" → skipped (recently processed)", flush=True)
        return {"status": "skipped", "reason": "Recently processed"}
    
    # Check if description already has our marker
    if ENHANCEMENT_MARKER in description:
        print(f"· [WH] Issue/create \"{title[:40]}\" → skipped (already enhanced)", flush=True)
        return {"status": "skipped", "reason": "Already enhanced"}
    
    # Skip if explicitly tagged to skip
    if "[skip=true]" in description:
        print(f"· [WH] Issue/create \"{title[:40]}\" → skipped (skip tag)", flush=True)
        return {"status": "skipped", "reason": "Skip tag present"}
    
    # Skip if project is in exclusion list
    if project_name and project_name.lower() in LINEAR_EXCLUDED_PROJECTS:
        print(f"· [WH] Issue/create \"{title[:40]}\" → skipped (excluded project: {project_name})", flush=True)
        return {"status": "skipped", "reason": f"Project '{project_name}' is excluded"}
    
    # Parse model selection from description
    model_shorthand = parse_model_tag(description)
    
    # Mark as processing to prevent loops
    _mark_as_processed(issue_id)
    
    print(f"", flush=True)
    print(f"▶ [WH] PROCESSING: \"{title}\"", flush=True)
    print(f"       ID: {issue_id} | Desc: {desc_len} chars | Model: {model_shorthand or 'default'}", flush=True)
    
    # Queue enhancement in background
    background_tasks.add_task(enhance_issue, issue_id, title, description, project_name, team_name, model_shorthand)
    
    return {"status": "queued", "issue_id": issue_id, "model": model_shorthand or "default"}


async def enhance_issue(
    issue_id: str, 
    title: str, 
    existing_description: str,
    project_name: str | None = None,
    team_name: str | None = None,
    model_shorthand: str | None = None,
):
    """Enhance an issue with AI-researched context."""
    print(f"\n{'='*60}", flush=True)
    print(f"🔍 Enhancing issue: {title}", flush=True)
    if project_name:
        print(f"   Project: {project_name}", flush=True)
    print(f"   Model: {model_shorthand or 'default'}", flush=True)
    print(f"{'='*60}\n", flush=True)
    
    # Add "working on it" comment - if this fails, the issue was likely deleted
    try:
        await add_comment(issue_id, "🔍 _Adding context to this issue now..._")
    except Exception as e:
        if "Entity not found" in str(e) or "not found" in str(e).lower():
            print(f"⚠️ Issue {issue_id} no longer exists, skipping enhancement", flush=True)
            return
        raise  # Re-raise other errors
    
    try:
        # Build prompt from title, project context, and existing description
        prompt = f"Issue: {title}"
        if project_name:
            prompt += f"\nLinear Project: {project_name}"
        if team_name:
            prompt += f"\nLinear Team: {team_name}"
        if existing_description:
            prompt += f"\n\nExisting notes:\n{existing_description}"
        
        # Always sync to ensure we have the latest data
        print("📥 Syncing data sources...", flush=True)
        await sync_all_async(DOCS_DIR)
        
        # Step 1: Research context from Slack/GDrive FIRST
        print("🔬 Step 1: Researching context (Slack/GDrive)...", flush=True)
        try:
            context = await _research_context(prompt, model_shorthand)
        except Exception as e:
            print(f"⚠️ Context research error: {e}", flush=True)
            context = f"Error researching context: {e}"
        
        # Step 2: Research codebase WITH context (so it knows about branches/PRs mentioned)
        print("🔬 Step 2: Researching codebase (with context)...", flush=True)
        with tempfile.TemporaryDirectory() as work_dir:
            try:
                code_analysis = await _research_codebase(prompt, context, work_dir, model_shorthand)
            except Exception as e:
                print(f"⚠️ Code research error: {e}", flush=True)
                code_analysis = f"Error researching code: {e}"
        
        # Generate enhanced description
        print("✍️ Writing enhanced description...", flush=True)
        enhanced = await _write_enhanced_description(title, existing_description, context, code_analysis, model_shorthand)
        
        # Add markers (includes original description for retry)
        markers = _build_enhancement_markers(existing_description)
        enhanced_with_marker = f"{enhanced}\n\n{markers}"
        
        # Update the Linear issue
        print(f"📝 Updating Linear issue...", flush=True)
        success = await update_issue_description(issue_id, enhanced_with_marker)
        
        if success:
            print(f"✅ Issue enhanced successfully!", flush=True)
            await add_comment(issue_id, "_✅ Issue enhanced with context from Slack, Google Drive, and GitHub._")
        else:
            print(f"❌ Failed to update issue via Linear API", flush=True)
            await add_comment(issue_id, "⚠️ _Failed to update issue description. Please check the logs._")
            
    except Exception as e:
        print(f"❌ Enhancement failed with error: {e}", flush=True)
        import traceback
        traceback.print_exc()
        await add_comment(issue_id, "❌ _Enhancement failed during issue processing. Please check server logs for details._")


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
        # No marker found - use empty string as original
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
        # Build prompt from title and original description
        prompt = f"Issue: {title}"
        if original_description:
            prompt += f"\n\nOriginal notes:\n{original_description}"
        
        # Sync data sources
        print("📥 Syncing data sources...", flush=True)
        await sync_all_async(DOCS_DIR)
        
        # Research context
        print("🔬 Step 1: Researching context (Slack/GDrive)...", flush=True)
        try:
            context = await _research_context(prompt, model_shorthand)
        except Exception as e:
            print(f"⚠️ Context research error: {e}", flush=True)
            context = f"Error researching context: {e}"
        
        # Research codebase
        print("🔬 Step 2: Researching codebase (with context)...", flush=True)
        with tempfile.TemporaryDirectory() as work_dir:
            try:
                code_analysis = await _research_codebase(prompt, context, work_dir, model_shorthand)
            except Exception as e:
                print(f"⚠️ Code research error: {e}", flush=True)
                code_analysis = f"Error researching code: {e}"
        
        # Generate new description with feedback context
        print("✍️ Writing enhanced description (with feedback)...", flush=True)
        enhanced = await _write_retry_description(
            title, original_description, ai_description, feedback, context, code_analysis, model_shorthand
        )
        
        # Add markers (preserve original description for future retries)
        markers = _build_enhancement_markers(original_description)
        enhanced_with_marker = f"{enhanced}\n\n{markers}"
        
        # Update the Linear issue
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
):
    """Answer a user's question using context and code research."""
    print(f"\n{'='*60}", flush=True)
    print(f"❓ Answering question for issue: {issue_id}", flush=True)
    print(f"   Model: {model_shorthand or 'default'}", flush=True)
    print(f"   User: {user_name}", flush=True)
    print(f"{'='*60}\n", flush=True)
    
    # Add "thinking" comment
    try:
        await add_comment(issue_id, "🤔 _Researching your question..._")
    except Exception as e:
        if "Entity not found" in str(e) or "not found" in str(e).lower():
            print(f"⚠️ Issue {issue_id} no longer exists, skipping answer", flush=True)
            return
        raise
    
    # Fetch issue data and comments for full context
    try:
        issue = await get_issue(issue_id)
        comments = await get_issue_comments(issue_id)
    except Exception as e:
        print(f"❌ Failed to fetch issue/comments: {e}", flush=True)
        await add_comment(issue_id, "❌ _Failed to fetch issue data. Please check server logs for details._")
        return
    
    # Build conversation context from comments
    comment_context = "\n\n".join([
        f"**{c.user_name}** ({c.created_at}):\n{c.body}"
        for c in comments
    ])
    
    try:
        # Sync data sources
        print("📥 Syncing data sources...", flush=True)
        await sync_all_async(DOCS_DIR)
        
        # Build the full prompt for the agent
        issue_context = f"""## Issue: {issue.title}

**Identifier:** {issue.identifier}
**Status:** {issue.state_name}
**Team:** {issue.team_name}

### Description
{issue.description or "_No description_"}

### Comment History
{comment_context or "_No previous comments_"}
"""
        
        # Research and answer the question
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
        
        # Format the response with user tag
        user_tag = f"@{user_name}" if user_name else ""
        response = f"{user_tag}\n\n{answer}" if user_tag else answer
        
        # Add the answer as a comment
        print(f"📝 Posting answer...", flush=True)
        success = await add_comment(issue_id, response)
        
        if success:
            print(f"✅ Question answered successfully!", flush=True)
        else:
            print(f"❌ Failed to post answer", flush=True)
            
    except Exception as e:
        print(f"❌ Answer failed with error: {e}", flush=True)
        import traceback
        traceback.print_exc()
        await add_comment(issue_id, "❌ _Failed to answer question. Please check server logs for details._")


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
    # Set up the repos directory and clear any previous state
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


def run_server(host: str = "0.0.0.0", port: int = 8000):
    """Run the API server."""
    import uvicorn
    print(f"🚀 Starting server on {host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    run_server()

