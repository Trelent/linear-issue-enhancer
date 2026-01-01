import asyncio
import os
import tempfile

from dotenv import load_dotenv
from agents import Runner
from agents.tracing import add_trace_processor

load_dotenv(override=True)

# Add console tracer for real-time logging
from src.tracing import ConsoleTracer
add_trace_processor(ConsoleTracer())

from src.agents import context_researcher, code_researcher, issue_writer
from src.sync import sync_all, sync_all_async, needs_sync


MAX_TURNS = 250


async def research_context(prompt: str, docs_dir: str) -> str:
    """Research context from markdown files."""
    result = await Runner.run(
        context_researcher,
        f"Find all context relevant to this issue:\n\n{prompt}\n\nSearch in: {docs_dir}",
        max_turns=MAX_TURNS,
    )
    return str(result.final_output)


async def research_codebase(prompt: str, repo: str | None, branch: str | None, work_dir: str) -> str:
    """Research the codebase for relevant context."""
    repo_dir = os.path.join(work_dir, "repo")

    if repo:
        clone_instruction = f"""Clone the repository: `{repo}`
Target directory: `{repo_dir}`"""
        if branch:
            clone_instruction += f"\nBranch: `{branch}`"
    else:
        clone_instruction = f"""No specific repository was provided. Use `list_github_repos` to discover 
available repositories, then identify which one is most relevant to the issue.

Once you've identified the repo, use `get_repo_info` to check its default branch,
then clone it to: `{repo_dir}`"""

    result = await Runner.run(
        code_researcher,
        f"""Analyze the codebase for the following issue:

## Issue
{prompt}

## Instructions
{clone_instruction}

Analyze the codebase and find all relevant code and context.""",
        max_turns=MAX_TURNS,
    )
    return str(result.final_output)


async def write_issue(prompt: str, context: str, code_analysis: str) -> str:
    """Write the final Linear issue."""
    result = await Runner.run(
        issue_writer,
        f"""Write a comprehensive Linear issue based on:

## Original Request
{prompt}

## Context from Slack/GDrive/Documents
{context}

## Codebase Analysis
{code_analysis}

Create a well-structured, actionable issue.""",
        max_turns=MAX_TURNS,
    )
    return str(result.final_output)


async def create_issue(
    prompt: str,
    docs_dir: str,
    repo: str | None = None,
    branch: str | None = None,
    slack_token: str | None = None,
    gdrive_creds: str | None = None,
    sync_max_age: int = 30,
) -> str:
    """Main function to create a Linear issue from all sources."""
    if needs_sync(docs_dir, max_age_minutes=sync_max_age):
        print("📥 Syncing data from Slack and Google Drive...")
        await sync_all_async(docs_dir, slack_token=slack_token, gdrive_creds=gdrive_creds)

    with tempfile.TemporaryDirectory() as work_dir:
        context, code_analysis = await asyncio.gather(
            research_context(prompt, docs_dir),
            research_codebase(prompt, repo, branch, work_dir),
        )
        return await write_issue(prompt, context, code_analysis)


def cmd_sync(args):
    """Run sync command."""
    print("📥 Syncing data sources...")
    updated = sync_all(
        args.docs,
        slack_token=os.getenv("SLACK_TOKEN") or args.slack_token,
        gdrive_creds=os.getenv("GDRIVE_CREDS") or args.gdrive_creds,
    )
    print("✅ Sync complete." + (" New data fetched." if updated else " No new data."))


async def cmd_issue(args):
    """Run issue creation command."""
    print("🔍 Starting issue research...\n")
    issue = await create_issue(
        prompt=args.prompt,
        docs_dir=args.docs,
        repo=args.repo,
        branch=args.branch,
        slack_token=os.getenv("SLACK_TOKEN") or args.slack_token,
        gdrive_creds=os.getenv("GDRIVE_CREDS") or args.gdrive_creds,
        sync_max_age=args.sync_max_age,
    )
    print("\n" + "=" * 80)
    print("📋 GENERATED ISSUE")
    print("=" * 80)
    print(issue)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Linear issue creation from Slack, GDrive, and GitHub")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Sync command
    sync_parser = subparsers.add_parser("sync", help="Sync data from Slack and Google Drive")
    sync_parser.add_argument("--docs", "-d", default="./data", help="Directory to store synced markdown files (default: ./data)")
    sync_parser.add_argument("--slack-token", help="Slack bot token (or set SLACK_TOKEN env var)")
    sync_parser.add_argument("--gdrive-creds", help="Path to Google Drive credentials JSON (or set GDRIVE_CREDS)")

    # Issue command
    issue_parser = subparsers.add_parser("issue", help="Create a Linear issue")
    issue_parser.add_argument("--prompt", "-p", required=True, help="Issue prompt/description")
    issue_parser.add_argument("--repo", "-r", help="GitHub repository (owner/repo). Omit to auto-discover.")
    issue_parser.add_argument("--branch", "-b", help="Branch to analyze (default: repo's default branch)")
    issue_parser.add_argument("--docs", "-d", default="./data", help="Directory with context files (default: ./data)")
    issue_parser.add_argument("--slack-token", help="Slack bot token (or set SLACK_TOKEN env var)")
    issue_parser.add_argument("--gdrive-creds", help="Path to Google Drive credentials JSON (or set GDRIVE_CREDS)")
    issue_parser.add_argument("--sync-max-age", type=int, default=30, help="Max age in minutes before re-syncing (default: 30)")

    # Serve command (API mode)
    serve_parser = subparsers.add_parser("serve", help="Run API server for Linear webhooks")
    serve_parser.add_argument("--host", default="0.0.0.0", help="Host to bind to (default: 0.0.0.0)")
    serve_parser.add_argument("--port", "-p", type=int, default=8000, help="Port to listen on (default: 8000)")

    args = parser.parse_args()

    if args.command == "sync":
        cmd_sync(args)
    elif args.command == "issue":
        asyncio.run(cmd_issue(args))
    elif args.command == "serve":
        from src.api import run_server
        run_server(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
