"""Shared utilities and constants for slash command tasks."""

import os
import tempfile

from agents import Runner

from src.agents import (
    create_context_researcher,
    create_code_researcher,
    create_issue_writer,
)
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


async def research_context(prompt: str, model_shorthand: str | None = None) -> str:
    """Research context from Slack/GDrive."""
    agent = create_context_researcher(model_shorthand)
    result = await Runner.run(
        agent,
        f"Find all context relevant to this issue:\n\n{prompt}\n\nSearch in: {DOCS_DIR}",
        max_turns=MAX_TURNS,
    )
    return str(result.final_output)


async def research_codebase(prompt: str, context: str, work_dir: str, model_shorthand: str | None = None) -> str:
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


async def write_enhanced_description(
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


async def write_retry_description(
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
