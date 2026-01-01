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

from src.linear import get_issue, update_issue_description, add_comment, LinearIssue
from src.agents import context_researcher, code_researcher, issue_writer
from src.sync import needs_sync, sync_all_async
from agents import Runner


MAX_TURNS = 250
DOCS_DIR = os.getenv("DOCS_DIR", "./data")
LINEAR_WEBHOOK_SECRET = os.getenv("LINEAR_WEBHOOK_SECRET")
SYNC_INTERVAL_HOURS = int(os.getenv("SYNC_INTERVAL_HOURS", "1"))

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


@app.post("/webhook/linear")
async def linear_webhook(request: Request, background_tasks: BackgroundTasks):
    """Handle Linear webhook events."""
    body = await request.body()
    signature = request.headers.get("linear-signature")
    
    if not _verify_signature(body, signature):
        print("❌ Webhook signature verification failed", flush=True)
        raise HTTPException(status_code=401, detail="Invalid signature")
    
    payload = await request.json()
    
    action = payload.get("action")
    event_type = payload.get("type")
    data = payload.get("data", {})
    
    print(f"📨 Webhook received: {event_type}/{action}", flush=True)
    
    # Only process issue creation events
    if event_type != "Issue" or action != "create":
        return {"status": "ignored", "reason": f"Not an issue create event: {event_type}/{action}"}
    
    issue_id = data.get("id")
    if not issue_id:
        raise HTTPException(status_code=400, detail="Missing issue ID")
    
    title = data.get("title", "")
    description = data.get("description") or ""
    
    # Check if we already processed this issue recently (prevents loops)
    if _was_recently_processed(issue_id):
        print(f"⏭️ Skipping {issue_id}: recently processed", flush=True)
        return {"status": "skipped", "reason": "Recently processed"}
    
    # Check if description already has our marker
    if ENHANCEMENT_MARKER in description:
        print(f"⏭️ Skipping {issue_id}: already enhanced", flush=True)
        return {"status": "skipped", "reason": "Already enhanced"}
    
    # Skip if description is already substantial (> 200 chars)
    if len(description) > 200:
        print(f"⏭️ Skipping {issue_id}: already has substantial description", flush=True)
        return {"status": "skipped", "reason": "Issue already has substantial description"}
    
    # Mark as processing to prevent loops
    _mark_as_processed(issue_id)
    
    print(f"✅ Queuing enhancement for: {title}", flush=True)
    
    # Queue enhancement in background
    background_tasks.add_task(enhance_issue, issue_id, title, description)
    
    return {"status": "queued", "issue_id": issue_id}


async def enhance_issue(issue_id: str, title: str, existing_description: str):
    """Enhance an issue with AI-researched context."""
    print(f"\n{'='*60}", flush=True)
    print(f"🔍 Enhancing issue: {title}", flush=True)
    print(f"{'='*60}\n", flush=True)
    
    try:
        # Build prompt from title and existing description
        prompt = f"Issue: {title}"
        if existing_description:
            prompt += f"\n\nExisting notes:\n{existing_description}"
        
        # Sync data if needed
        if needs_sync(DOCS_DIR, max_age_minutes=30):
            print("📥 Syncing data sources...", flush=True)
            await sync_all_async(DOCS_DIR)
        
        # Research context and codebase in parallel
        with tempfile.TemporaryDirectory() as work_dir:
            print("🔬 Starting research (context + code)...", flush=True)
            context_result, code_result = await asyncio.gather(
                _research_context(prompt),
                _research_codebase(prompt, work_dir),
                return_exceptions=True,
            )
            
            # Handle any errors with detailed logging
            if isinstance(context_result, Exception):
                print(f"⚠️ Context research error: {context_result}", flush=True)
                context = f"Error researching context: {context_result}"
            else:
                context = str(context_result)
                
            if isinstance(code_result, Exception):
                print(f"⚠️ Code research error: {code_result}", flush=True)
                code_analysis = f"Error researching code: {code_result}"
            else:
                code_analysis = str(code_result)
        
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
            await add_comment(issue_id, "_This issue was automatically enhanced with context from Slack, Google Drive, and GitHub._")
        else:
            print(f"❌ Failed to update issue via Linear API", flush=True)
            
    except Exception as e:
        print(f"❌ Enhancement failed with error: {e}", flush=True)
        import traceback
        traceback.print_exc()


async def _research_context(prompt: str) -> str:
    """Research context from Slack/GDrive."""
    result = await Runner.run(
        context_researcher,
        f"Find all context relevant to this issue:\n\n{prompt}\n\nSearch in: {DOCS_DIR}",
        max_turns=MAX_TURNS,
    )
    return str(result.final_output)


async def _research_codebase(prompt: str, work_dir: str) -> str:
    """Research the codebase."""
    repo_dir = os.path.join(work_dir, "repo")
    
    result = await Runner.run(
        code_researcher,
        f"""Analyze the codebase for the following issue:

## Issue
{prompt}

## Instructions
No specific repository was provided. Use `list_github_repos` to discover 
available repositories, then identify which one is most relevant to the issue.

Once you've identified the repo, use `get_repo_info` to check its default branch,
then clone it to: `{repo_dir}`

Analyze the codebase and find all relevant code and context.""",
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

