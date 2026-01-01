"""GitHub repository cache with README fetching and recency ordering."""

import json
import subprocess
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path

CACHE_FILE = Path(__file__).parent.parent / "data" / "github_cache.json"
CACHE_TTL_HOURS = 1


@dataclass
class RepoInfo:
    name: str
    description: str
    default_branch: str
    pushed_at: str
    readme_summary: str
    url: str


def _parse_iso_date(date_str: str) -> datetime:
    """Parse ISO date string, handling various formats."""
    if not date_str:
        return datetime.min
    date_str = date_str.replace("Z", "+00:00")
    return datetime.fromisoformat(date_str)


def _time_ago(date_str: str) -> str:
    """Convert ISO date to human-readable 'time ago' string."""
    if not date_str:
        return "unknown"
    dt = _parse_iso_date(date_str)
    now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
    delta = now - dt

    if delta.days > 365:
        return f"{delta.days // 365}y ago"
    if delta.days > 30:
        return f"{delta.days // 30}mo ago"
    if delta.days > 0:
        return f"{delta.days}d ago"
    if delta.seconds > 3600:
        return f"{delta.seconds // 3600}h ago"
    return f"{delta.seconds // 60}m ago"


def _load_cache() -> dict:
    """Load cache from disk."""
    if not CACHE_FILE.exists():
        return {"repos": {}, "last_updated": None}
    return json.loads(CACHE_FILE.read_text())


def _save_cache(cache: dict) -> None:
    """Save cache to disk."""
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=2, default=str))


def _is_cache_valid(cache: dict) -> bool:
    """Check if cache is still valid."""
    if not cache.get("last_updated"):
        return False
    last_updated = datetime.fromisoformat(cache["last_updated"])
    return datetime.now() - last_updated < timedelta(hours=CACHE_TTL_HOURS)


def _fetch_readme(repo: str) -> str:
    """Fetch and summarize a repo's README."""
    result = subprocess.run(
        ["gh", "api", f"repos/{repo}/readme", "--jq", ".content"],
        capture_output=True, text=True, timeout=15
    )
    if result.returncode != 0:
        return "_No README available_"

    import base64
    content = result.stdout.strip()
    if not content:
        return "_No README available_"

    decoded = base64.b64decode(content).decode("utf-8", errors="ignore")

    # Extract first meaningful paragraph (skip badges, titles)
    lines = decoded.splitlines()
    summary_lines = []
    in_content = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if in_content and summary_lines:
                break
            continue
        # Skip badges, images, HTML
        if stripped.startswith(("![", "<", "[!", "<!--", "# ")):
            continue
        # Skip short lines that are likely headers or badges
        if len(stripped) < 20:
            continue
        in_content = True
        summary_lines.append(stripped)
        if len(" ".join(summary_lines)) > 300:
            break

    summary = " ".join(summary_lines)[:400]
    return summary.strip() if summary else "_No description in README_"


def _fetch_repos(org: str) -> list[RepoInfo]:
    """Fetch all repos with metadata, ordered by recency."""
    cmd = ["gh", "repo", "list"]
    if org:
        cmd.append(org)
    cmd.extend([
        "--limit", "100",
        "--json", "nameWithOwner,description,defaultBranchRef,pushedAt,url",
        "--jq", "sort_by(.pushedAt) | reverse"
    ])

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        return []

    repos_data = json.loads(result.stdout)
    repos = []

    for repo_data in repos_data:
        name = repo_data.get("nameWithOwner", "")
        branch_ref = repo_data.get("defaultBranchRef") or {}

        # Fetch README for each repo
        readme = _fetch_readme(name)

        repos.append(RepoInfo(
            name=name,
            description=repo_data.get("description") or "",
            default_branch=branch_ref.get("name", "main"),
            pushed_at=repo_data.get("pushedAt", ""),
            readme_summary=readme,
            url=repo_data.get("url", ""),
        ))

    return repos


def get_repos(org: str = "", force_refresh: bool = False) -> list[RepoInfo]:
    """Get repos from cache or fetch fresh data."""
    cache = _load_cache()
    cache_key = org or "__all__"

    if not force_refresh and _is_cache_valid(cache) and cache_key in cache.get("repos", {}):
        return [RepoInfo(**r) for r in cache["repos"][cache_key]]

    repos = _fetch_repos(org)

    cache["repos"] = cache.get("repos", {})
    cache["repos"][cache_key] = [asdict(r) for r in repos]
    cache["last_updated"] = datetime.now().isoformat()
    _save_cache(cache)

    return repos


def format_repos_markdown(repos: list[RepoInfo], org: str = "") -> str:
    """Format repos as Markdown optimized for LLM consumption."""
    if not repos:
        return "## No Repositories Found\n\nNo accessible repositories match your query."

    lines = [
        f"## GitHub Repositories{f' ({org})' if org else ''}",
        "",
        f"Found **{len(repos)}** repositories, ordered by most recent activity:",
        "",
    ]

    for repo in repos:
        time_ago = _time_ago(repo.pushed_at)
        lines.append(f"### `{repo.name}` _{time_ago}_")
        lines.append("")
        lines.append(f"| Property | Value |")
        lines.append(f"|----------|-------|")
        lines.append(f"| **Default Branch** | `{repo.default_branch}` |")
        lines.append(f"| **Last Push** | {time_ago} |")
        lines.append(f"| **URL** | {repo.url} |")
        lines.append("")

        if repo.description:
            lines.append(f"**Description:** {repo.description}")
            lines.append("")

        lines.append(f"**README Summary:**")
        lines.append(f"> {repo.readme_summary}")
        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)

