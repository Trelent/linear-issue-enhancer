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

from src.linear import update_issue_description, add_comment
from src.agents import context_researcher, code_researcher, issue_writer
from src.sync import sync_all_async
from src.tools import set_repos_base_dir, clear_cloned_repos
from agents import Runner


MAX_TURNS = 250
DOCS_DIR = os.getenv("DOCS_DIR", "./data")
LINEAR_WEBHOOK_SECRET = os.getenv("LINEAR_WEBHOOK_SECRET")
SYNC_INTERVAL_HOURS = int(os.getenv("SYNC_INTERVAL_HOURS", "1"))
SLACK_TOKEN = os.getenv("SLACK_TOKEN")
GDRIVE_CREDS = os.getenv("GDRIVE_CREDS")

# For deployed environments: decode base64 gdrive creds to a temp file
if not GDRIVE_CREDS and os.getenv("GDRIVE_CREDS_BASE64"):
    import base64
    import tempfile
    creds_json = base64.b64decode(os.getenv("GDRIVE_CREDS_BASE64")).decode()
    creds_file = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    creds_file.write(creds_json)
    creds_file.close()
    GDRIVE_CREDS = creds_file.name
    print(f"📄 GDrive credentials decoded to temp file", flush=True)

# Track recently processed issues to prevent infinite loops
# Key: issue_id, Value: timestamp
_recently_processed: dict[str, float] = {}
PROCESS_COOLDOWN_SECONDS = 300  # 5 minutes

# Marker we add to enhanced descriptions
ENHANCEMENT_MARKER = "<!-- enhanced-by-linear-enhancer -->"

# Scheduler instance
scheduler = AsyncIOScheduler()


async def scheduled_sync():
    """Run periodic sync of data sources."""
    print(f"\n{'='*60}", flush=True)
    print("⏰ Scheduled sync starting...", flush=True)
    print(f"{'='*60}\n", flush=True)
    try:
        await sync_all_async(DOCS_DIR, slack_token=SLACK_TOKEN, gdrive_creds=GDRIVE_CREDS)
        print("✅ Scheduled sync complete!", flush=True)
    except Exception as e:
        print(f"❌ Scheduled sync failed: {e}", flush=True)
        import traceback
        traceback.print_exc()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """App lifespan handler."""
    print("🚀 Linear Enhancer API starting...", flush=True)
    print(f"   Slack token: {'✓' if SLACK_TOKEN else '✗ (not set)'}", flush=True)
    print(f"   GDrive creds: {'✓' if GDRIVE_CREDS else '✗ (not set)'}", flush=True)
    
    # Run initial sync on boot
    print("📥 Running initial sync on boot...", flush=True)
    try:
        await sync_all_async(DOCS_DIR, slack_token=SLACK_TOKEN, gdrive_creds=GDRIVE_CREDS)
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
    issue_id = data.get("id", "?")
    title = data.get("title", "?")
    
    # Only process issue creation events
    if event_type != "Issue" or action != "create":
        print(f"· [WH] {event_type}/{action} → ignored", flush=True)
        return {"status": "ignored", "reason": f"Not an issue create event: {event_type}/{action}"}
    
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
    
    # Skip if description is already substantial (> 1000 chars)
    if desc_len > 1000:
        print(f"· [WH] Issue/create \"{title[:40]}\" → skipped (desc {desc_len} chars)", flush=True)
        return {"status": "skipped", "reason": "Issue already has substantial description"}
    
    # Mark as processing to prevent loops
    _mark_as_processed(issue_id)
    
    print(f"", flush=True)
    print(f"▶ [WH] PROCESSING: \"{title}\"", flush=True)
    print(f"       ID: {issue_id} | Desc: {desc_len} chars", flush=True)
    
    # Queue enhancement in background
    background_tasks.add_task(enhance_issue, issue_id, title, description, project_name, team_name)
    
    return {"status": "queued", "issue_id": issue_id}


async def enhance_issue(
    issue_id: str, 
    title: str, 
    existing_description: str,
    project_name: str | None = None,
    team_name: str | None = None,
):
    """Enhance an issue with AI-researched context."""
    print(f"\n{'='*60}", flush=True)
    print(f"🔍 Enhancing issue: {title}", flush=True)
    if project_name:
        print(f"   Project: {project_name}", flush=True)
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
        await sync_all_async(DOCS_DIR, slack_token=SLACK_TOKEN, gdrive_creds=GDRIVE_CREDS)
        
        # Step 1: Research context from Slack/GDrive FIRST
        print("🔬 Step 1: Researching context (Slack/GDrive)...", flush=True)
        try:
            context = await _research_context(prompt)
        except Exception as e:
            print(f"⚠️ Context research error: {e}", flush=True)
            context = f"Error researching context: {e}"
        
        # Step 2: Research codebase WITH context (so it knows about branches/PRs mentioned)
        print("🔬 Step 2: Researching codebase (with context)...", flush=True)
        with tempfile.TemporaryDirectory() as work_dir:
            try:
                code_analysis = await _research_codebase(prompt, context, work_dir)
            except Exception as e:
                print(f"⚠️ Code research error: {e}", flush=True)
                code_analysis = f"Error researching code: {e}"
        
        # Generate enhanced description
        print("✍️ Writing enhanced description...", flush=True)
        enhanced = await _write_enhanced_description(title, existing_description, context, code_analysis)
        
        # Add marker to prevent re-processing
        enhanced_with_marker = f"{enhanced}\n\n{ENHANCEMENT_MARKER}"
        
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
        await add_comment(issue_id, f"❌ _Enhancement failed: {e}_")


async def _research_context(prompt: str) -> str:
    """Research context from Slack/GDrive."""
    result = await Runner.run(
        context_researcher,
        f"Find all context relevant to this issue:\n\n{prompt}\n\nSearch in: {DOCS_DIR}",
        max_turns=MAX_TURNS,
    )
    return str(result.final_output)


async def _research_codebase(prompt: str, context: str, work_dir: str) -> str:
    """Research the codebase, informed by context from Slack/GDrive."""
    # Set up the repos directory and clear any previous state
    repos_dir = os.path.join(work_dir, "repos")
    clear_cloned_repos()
    set_repos_base_dir(repos_dir)
    
    result = await Runner.run(
        code_researcher,
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
) -> str:
    """Generate an enhanced issue description."""
    result = await Runner.run(
        issue_writer,
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


def run_server(host: str = "0.0.0.0", port: int = 8000):
    """Run the API server."""
    import uvicorn
    print(f"🚀 Starting server on {host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    run_server()

