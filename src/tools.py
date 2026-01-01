import json
import subprocess
import shutil
from pathlib import Path

from agents import function_tool

from src.github_cache import get_repos, format_repos_markdown


# -----------------------------------------------------------------------------
# GitHub CLI Tools
# -----------------------------------------------------------------------------

@function_tool
def list_github_repos(org: str = "", force_refresh: bool = False) -> str:
    """List GitHub repositories with README summaries, ordered by recent activity.

    Results are cached for 1 hour to avoid repeated API calls.

    Args:
        org: Optional org/user to filter by. If empty, lists all accessible repos.
        force_refresh: If True, bypass cache and fetch fresh data.
    """
    repos = get_repos(org=org, force_refresh=force_refresh)
    return format_repos_markdown(repos, org=org)


@function_tool
def get_repo_info(repo: str) -> str:
    """Get detailed info about a specific repository.

    Args:
        repo: The repository in owner/repo format (e.g., Trelent/linear-enhancer).
    """
    result = subprocess.run(
        ["gh", "repo", "view", repo, "--json", "name,description,defaultBranchRef,url,languages,pushedAt"],
        capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        return f"## ❌ Error\n\n```\n{result.stderr.strip()}\n```"

    info = json.loads(result.stdout)
    branch_ref = info.get("defaultBranchRef") or {}
    default_branch = branch_ref.get("name", "main")
    languages = info.get("languages") or []
    lang_list = ", ".join(f"`{lang['node']['name']}`" for lang in languages[:5]) if languages else "_Unknown_"

    lines = [
        f"## Repository: `{repo}`",
        "",
        f"| Property | Value |",
        f"|----------|-------|",
        f"| **URL** | {info.get('url', 'N/A')} |",
        f"| **Default Branch** | `{default_branch}` |",
        f"| **Languages** | {lang_list} |",
        f"| **Last Push** | {info.get('pushedAt', 'N/A')} |",
        "",
        f"**Description:** {info.get('description') or '_No description_'}",
    ]
    return "\n".join(lines)


@function_tool
def list_repo_branches(repo: str) -> str:
    """List branches for a GitHub repository.

    Args:
        repo: The repository in owner/repo format (e.g., Trelent/backend).
    """
    result = subprocess.run(
        ["gh", "api", f"repos/{repo}/branches", "--paginate"],
        capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        return f"## ❌ Error\n\n```\n{result.stderr.strip()}\n```"

    branches = json.loads(result.stdout)
    if not branches:
        return f"## Branches for `{repo}`\n\nNo branches found."

    lines = [f"## Branches for `{repo}`", "", f"Found **{len(branches)}** branches:", ""]
    for branch in branches:
        name = branch.get("name", "unknown")
        protected = "🔒" if branch.get("protected") else ""
        lines.append(f"- `{name}` {protected}")

    return "\n".join(lines)


@function_tool
def list_prs(repo: str, state: str = "open") -> str:
    """List pull requests for a repository, ordered by most recent activity.

    Args:
        repo: The repository in owner/repo format (e.g., Trelent/backend).
        state: Filter by state: "open", "closed", "merged", or "all" (default: open).
    """
    result = subprocess.run(
        [
            "gh", "pr", "list",
            "--repo", repo,
            "--state", state,
            "--limit", "25",
            "--json", "number,title,author,headRefName,baseRefName,updatedAt,additions,deletions,state"
        ],
        capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        return f"## ❌ Error\n\n```\n{result.stderr.strip()}\n```"

    prs = json.loads(result.stdout)
    if not prs:
        return f"## Pull Requests for `{repo}`\n\nNo {state} pull requests found."

    # Sort by updatedAt descending
    prs.sort(key=lambda p: p.get("updatedAt", ""), reverse=True)

    lines = [
        f"## Pull Requests for `{repo}` ({state})",
        "",
        f"Found **{len(prs)}** pull requests:",
        "",
    ]

    for pr in prs:
        number = pr.get("number", "?")
        title = pr.get("title", "Untitled")
        author = pr.get("author", {}).get("login", "unknown")
        head = pr.get("headRefName", "?")
        base = pr.get("baseRefName", "?")
        additions = pr.get("additions", 0)
        deletions = pr.get("deletions", 0)

        lines.append(f"### #{number}: {title}")
        lines.append("")
        lines.append(f"| Property | Value |")
        lines.append(f"|----------|-------|")
        lines.append(f"| **Author** | @{author} |")
        lines.append(f"| **Branch** | `{head}` → `{base}` |")
        lines.append(f"| **Changes** | +{additions} / -{deletions} |")
        lines.append("")

    return "\n".join(lines)


@function_tool
def get_pr_details(repo: str, pr_number: int, include_diff: bool = False) -> str:
    """Get detailed information about a specific pull request.

    Args:
        repo: The repository in owner/repo format (e.g., Trelent/backend).
        pr_number: The PR number to fetch.
        include_diff: If True, include the full diff (can be large).
    """
    result = subprocess.run(
        [
            "gh", "pr", "view", str(pr_number),
            "--repo", repo,
            "--json", "number,title,body,author,headRefName,baseRefName,state,additions,deletions,files,comments,reviews"
        ],
        capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        return f"## ❌ Error\n\n```\n{result.stderr.strip()}\n```"

    pr = json.loads(result.stdout)
    number = pr.get("number", pr_number)
    title = pr.get("title", "Untitled")
    body = pr.get("body", "_No description_") or "_No description_"
    author = pr.get("author", {}).get("login", "unknown")
    head = pr.get("headRefName", "?")
    base = pr.get("baseRefName", "?")
    state = pr.get("state", "UNKNOWN")
    additions = pr.get("additions", 0)
    deletions = pr.get("deletions", 0)
    files = pr.get("files", []) or []
    comments = pr.get("comments", []) or []
    reviews = pr.get("reviews", []) or []

    lines = [
        f"## PR #{number}: {title}",
        "",
        f"| Property | Value |",
        f"|----------|-------|",
        f"| **Author** | @{author} |",
        f"| **State** | {state} |",
        f"| **Branch** | `{head}` → `{base}` |",
        f"| **Changes** | +{additions} / -{deletions} across {len(files)} files |",
        "",
        "### Description",
        "",
        body,
        "",
    ]

    # Files changed
    if files:
        lines.append("### Files Changed")
        lines.append("")
        for f in files[:30]:
            path = f.get("path", "?")
            adds = f.get("additions", 0)
            dels = f.get("deletions", 0)
            lines.append(f"- `{path}` (+{adds} / -{dels})")
        if len(files) > 30:
            lines.append(f"- _...and {len(files) - 30} more files_")
        lines.append("")

    # Reviews
    if reviews:
        lines.append("### Reviews")
        lines.append("")
        for review in reviews[:10]:
            reviewer = review.get("author", {}).get("login", "unknown")
            review_state = review.get("state", "PENDING")
            review_body = review.get("body", "")
            emoji = {"APPROVED": "✅", "CHANGES_REQUESTED": "❌", "COMMENTED": "💬"}.get(review_state, "⏳")
            lines.append(f"- {emoji} **@{reviewer}** — {review_state}")
            if review_body:
                lines.append(f"  > {review_body[:200]}")
        lines.append("")

    # Comments
    if comments:
        lines.append("### Comments")
        lines.append("")
        for comment in comments[:10]:
            commenter = comment.get("author", {}).get("login", "unknown")
            comment_body = comment.get("body", "")[:300]
            lines.append(f"> **@{commenter}:** {comment_body}")
            lines.append("")
        if len(comments) > 10:
            lines.append(f"_...and {len(comments) - 10} more comments_")
            lines.append("")

    # Diff (optional)
    if include_diff:
        diff_result = subprocess.run(
            ["gh", "pr", "diff", str(pr_number), "--repo", repo],
            capture_output=True, text=True, timeout=60
        )
        if diff_result.returncode == 0:
            diff_lines = diff_result.stdout.splitlines()
            lines.append("### Diff")
            lines.append("")
            lines.append("```diff")
            lines.extend(diff_lines[:500])
            if len(diff_lines) > 500:
                lines.append(f"... truncated ({len(diff_lines) - 500} more lines)")
            lines.append("```")

    return "\n".join(lines)


# -----------------------------------------------------------------------------
# File & Directory Tools
# -----------------------------------------------------------------------------

@function_tool
def grep_files(pattern: str, directory: str, file_glob: str = "*.md") -> str:
    """Search for a pattern in files using grep.

    Args:
        pattern: The regex pattern to search for.
        directory: The directory to search in.
        file_glob: File pattern to match (default: *.md).
    """
    result = subprocess.run(
        ["grep", "-r", "-n", "-i", "--include", file_glob, pattern, directory],
        capture_output=True, text=True, timeout=30
    )
    output = result.stdout.strip()

    if not output:
        return f"## Search Results\n\nNo matches found for `{pattern}` in `{directory}` ({file_glob})."

    # Group results by file
    matches: dict[str, list[str]] = {}
    for line in output.splitlines():
        if ":" in line:
            parts = line.split(":", 2)
            if len(parts) >= 3:
                filepath, lineno, content = parts[0], parts[1], parts[2]
                matches.setdefault(filepath, []).append(f"  {lineno}: {content.strip()}")

    lines = [f"## Search Results for `{pattern}`", "", f"Found matches in **{len(matches)}** files:", ""]
    for filepath, file_matches in list(matches.items())[:20]:
        lines.append(f"### `{filepath}`")
        lines.append("```")
        lines.extend(file_matches[:10])
        if len(file_matches) > 10:
            lines.append(f"  ... and {len(file_matches) - 10} more matches")
        lines.append("```")
        lines.append("")

    return "\n".join(lines)


@function_tool
def read_file_content(file_path: str, max_lines: int = 200) -> str:
    """Read the contents of a file.

    Args:
        file_path: Path to the file to read.
        max_lines: Maximum number of lines to return (default: 200).
    """
    path = Path(file_path)
    if not path.exists():
        return f"## ❌ File Not Found\n\n`{file_path}` does not exist."

    content = path.read_text()
    lines = content.splitlines()
    total_lines = len(lines)
    truncated = total_lines > max_lines

    ext = path.suffix.lstrip(".") or "txt"
    lang_map = {"py": "python", "js": "javascript", "ts": "typescript", "md": "markdown", "yml": "yaml"}
    lang = lang_map.get(ext, ext)

    output_lines = [
        f"## File: `{file_path}`",
        "",
        f"**Lines:** {total_lines}" + (f" (showing first {max_lines})" if truncated else ""),
        "",
        f"```{lang}",
    ]
    output_lines.extend(lines[:max_lines])
    output_lines.append("```")

    if truncated:
        output_lines.append(f"\n_...truncated {total_lines - max_lines} lines_")

    return "\n".join(output_lines)


@function_tool
def list_directory(directory: str) -> str:
    """List files and directories in a path.

    Args:
        directory: The directory to list.
    """
    path = Path(directory)
    if not path.exists():
        return f"## ❌ Directory Not Found\n\n`{directory}` does not exist."

    items = sorted(path.iterdir())
    dirs = [item for item in items if item.is_dir()]
    files = [item for item in items if item.is_file()]

    lines = [f"## Directory: `{directory}`", "", f"**{len(dirs)}** directories, **{len(files)}** files", ""]

    if dirs:
        lines.append("### 📁 Directories")
        for d in dirs[:50]:
            lines.append(f"- `{d.name}/`")
        lines.append("")

    if files:
        lines.append("### 📄 Files")
        for f in files[:50]:
            size = f.stat().st_size
            size_str = f"{size:,} bytes" if size < 10000 else f"{size / 1024:.1f} KB"
            lines.append(f"- `{f.name}` ({size_str})")

    if len(items) > 100:
        lines.append(f"\n_...and {len(items) - 100} more items_")

    return "\n".join(lines)


# -----------------------------------------------------------------------------
# Git Tools
# -----------------------------------------------------------------------------

@function_tool
def clone_repo(repo: str, target_dir: str, branch: str = "") -> str:
    """Clone a GitHub repository.

    Args:
        repo: The repository URL or owner/repo format (e.g., Trelent/backend).
        target_dir: The directory to clone into.
        branch: Specific branch to clone (default: repo's default branch).
    """
    import os
    
    if Path(target_dir).exists():
        shutil.rmtree(target_dir)

    # Normalize repo to URL, using GH_TOKEN for auth if available
    gh_token = os.getenv("GH_TOKEN")
    if repo.startswith("http"):
        repo_url = repo
    elif gh_token:
        # Use token for private repo access
        repo_url = f"https://{gh_token}@github.com/{repo}.git"
    else:
        repo_url = f"https://github.com/{repo}"

    cmd = ["git", "clone", "--depth", "1"]
    if branch:
        cmd.extend(["--branch", branch])
    cmd.extend([repo_url, target_dir])

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        # Redact token from error message if present
        stderr = result.stderr.strip()
        if gh_token:
            stderr = stderr.replace(gh_token, "[REDACTED]")
        return f"## ❌ Clone Failed\n\nRepository: `{repo}`\nBranch: `{branch or 'default'}`\n\n```\n{stderr}\n```\n\n_Hint: Check GH_TOKEN is set and has repo access._"

    # Get some info about what was cloned
    cloned_path = Path(target_dir)
    file_count = sum(1 for _ in cloned_path.rglob("*") if _.is_file())
    dir_count = sum(1 for _ in cloned_path.rglob("*") if _.is_dir())

    lines = [
        f"## ✅ Repository Cloned",
        "",
        f"| Property | Value |",
        f"|----------|-------|",
        f"| **Repository** | `{repo}` |",
        f"| **Branch** | `{branch or 'default'}` |",
        f"| **Location** | `{target_dir}` |",
        f"| **Files** | {file_count} |",
        f"| **Directories** | {dir_count} |",
    ]
    return "\n".join(lines)
