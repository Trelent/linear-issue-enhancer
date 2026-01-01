"""FastAPI webhook server for Linear integration."""

import asyncio
import hashlib
import hmac
import os
import tempfile
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """App lifespan handler."""
    print("🚀 Linear Enhancer API starting...")
    yield
    print("👋 Shutting down...")


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


@app.post("/webhook/linear")
async def linear_webhook(request: Request, background_tasks: BackgroundTasks):
    """Handle Linear webhook events."""
    body = await request.body()
    signature = request.headers.get("linear-signature")
    
    if not _verify_signature(body, signature):
        raise HTTPException(status_code=401, detail="Invalid signature")
    
    payload = await request.json()
    
    action = payload.get("action")
    event_type = payload.get("type")
    data = payload.get("data", {})
    
    # Only process issue creation events
    if event_type != "Issue" or action != "create":
        return {"status": "ignored", "reason": f"Not an issue create event: {event_type}/{action}"}
    
    issue_id = data.get("id")
    if not issue_id:
        raise HTTPException(status_code=400, detail="Missing issue ID")
    
    # Check if this looks like a stub issue (short/empty description)
    title = data.get("title", "")
    description = data.get("description") or ""
    
    # Skip if description is already substantial (> 200 chars)
    if len(description) > 200:
        return {"status": "skipped", "reason": "Issue already has substantial description"}
    
    # Queue enhancement in background
    background_tasks.add_task(enhance_issue, issue_id, title, description)
    
    return {"status": "queued", "issue_id": issue_id}


async def enhance_issue(issue_id: str, title: str, existing_description: str):
    """Enhance an issue with AI-researched context."""
    print(f"\n{'='*60}")
    print(f"🔍 Enhancing issue: {title}")
    print(f"{'='*60}\n")
    
    # Build prompt from title and existing description
    prompt = f"Issue: {title}"
    if existing_description:
        prompt += f"\n\nExisting notes:\n{existing_description}"
    
    # Sync data if needed
    if needs_sync(DOCS_DIR, max_age_minutes=30):
        print("📥 Syncing data sources...")
        await sync_all_async(DOCS_DIR)
    
    # Research context and codebase in parallel
    with tempfile.TemporaryDirectory() as work_dir:
        context_result, code_result = await asyncio.gather(
            _research_context(prompt),
            _research_codebase(prompt, work_dir),
            return_exceptions=True,
        )
        
        # Handle any errors
        context = str(context_result) if not isinstance(context_result, Exception) else f"Error: {context_result}"
        code_analysis = str(code_result) if not isinstance(code_result, Exception) else f"Error: {code_result}"
    
    # Generate enhanced description
    enhanced = await _write_enhanced_description(title, existing_description, context, code_analysis)
    
    # Update the Linear issue
    print(f"\n📝 Updating Linear issue...")
    success = await update_issue_description(issue_id, enhanced)
    
    if success:
        print(f"✅ Issue enhanced successfully!")
        # Add a comment noting the enhancement
        await add_comment(issue_id, "_This issue was automatically enhanced with context from Slack, Google Drive, and GitHub._")
    else:
        print(f"❌ Failed to update issue")


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

Write a comprehensive, well-structured issue description. Include:
- Clear problem statement
- Relevant context from communications
- Technical details from code analysis  
- Acceptance criteria if determinable
- Any relevant file paths or code references

Do NOT include a title - just write the description body.
Format using Markdown.""",
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

