"""FastAPI webhook server for Linear integration."""

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
from src.agents import parse_model_tag
from src.sync import sync_all_async, print_connector_status
from src.commands import dispatch_command
from src.commands.shared import (
    DOCS_DIR,
    ENHANCEMENT_MARKER,
    _build_enhancement_markers,
    research_context,
    research_codebase,
    write_enhanced_description,
)


LINEAR_WEBHOOK_SECRET = os.getenv("LINEAR_WEBHOOK_SECRET")
SYNC_INTERVAL_HOURS = int(os.getenv("SYNC_INTERVAL_HOURS", "1"))

# Comma-separated list of Linear project names to exclude from enhancement
_excluded_projects_raw = os.getenv("LINEAR_EXCLUDED_PROJECTS", "")
LINEAR_EXCLUDED_PROJECTS = {p.strip().lower() for p in _excluded_projects_raw.split(",") if p.strip()}

# Track recently processed issues to prevent infinite loops
_recently_processed: dict[str, float] = {}
PROCESS_COOLDOWN_SECONDS = 300  # 5 minutes

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
    """Handle comment creation - dispatch to slash command registry."""
    comment_body = data.get("body") or ""
    comment_id = data.get("id")
    issue_data = data.get("issue", {})
    issue_id = issue_data.get("id")
    issue_identifier = issue_data.get("identifier", "?")
    user_data = data.get("user", {})
    user_id = user_data.get("id", "")
    user_name = user_data.get("displayName", "")
    
    # Extract parent comment ID if this is a reply
    parent_data = data.get("parent", {})
    parent_comment_id = parent_data.get("id") if parent_data else None
    
    print(f"· [WH] Comment/create on {issue_identifier}: \"{comment_body[:50]}{'...' if len(comment_body) > 50 else ''}\"", flush=True)
    if parent_comment_id:
        print(f"       (reply to comment {parent_comment_id})", flush=True)
    
    if not issue_id:
        print(f"  → Missing issue ID in payload, ignored", flush=True)
        return {"status": "ignored", "reason": "Missing issue ID in comment"}
    
    # Dispatch to command registry
    result = await dispatch_command(
        comment_body=comment_body,
        issue_id=issue_id,
        issue_identifier=issue_identifier,
        user_id=user_id,
        user_name=user_name,
        background_tasks=background_tasks,
        comment_id=comment_id,
        parent_comment_id=parent_comment_id,
    )
    
    if result is None:
        print(f"  → Not a slash command, ignored", flush=True)
        return {"status": "ignored", "reason": "Not a slash command"}
    
    return {
        "status": result.status,
        "action": result.action,
        "issue_id": result.issue_id,
        "model": result.model,
    }


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
        raise
    
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
            context = await research_context(prompt, model_shorthand)
        except Exception as e:
            print(f"⚠️ Context research error: {e}", flush=True)
            context = f"Error researching context: {e}"
        
        # Step 2: Research codebase WITH context (so it knows about branches/PRs mentioned)
        print("🔬 Step 2: Researching codebase (with context)...", flush=True)
        with tempfile.TemporaryDirectory() as work_dir:
            try:
                code_analysis = await research_codebase(prompt, context, work_dir, model_shorthand)
            except Exception as e:
                print(f"⚠️ Code research error: {e}", flush=True)
                code_analysis = f"Error researching code: {e}"
        
        # Generate enhanced description
        print("✍️ Writing enhanced description...", flush=True)
        enhanced = await write_enhanced_description(title, existing_description, context, code_analysis, model_shorthand)
        
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


def run_server(host: str = "0.0.0.0", port: int = 8000):
    """Run the API server."""
    import uvicorn
    print(f"🚀 Starting server on {host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    run_server()
