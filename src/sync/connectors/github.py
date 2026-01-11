"""GitHub connector - syncs repository info and READMEs."""

import json
import subprocess
import base64
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path

from src.sync.connector import Connector, ConnectorResult
from src.sync import StateManager


CACHE_TTL_HOURS = 1


@dataclass
class RepoInfo:
    name: str
    description: str
    default_branch: str
    pushed_at: str
    readme_summary: str
    url: str


class GitHubConnector(Connector):
    """Syncs GitHub repository metadata and README summaries."""
    
    name = "github"
    env_key = "GH_TOKEN"
    
    def __init__(self):
        super().__init__()
        self._org: str = ""
    
    @property
    def enabled(self) -> bool:
        # GitHub uses GH_TOKEN or local gh CLI auth
        # Check if gh CLI is available and authenticated
        if os.getenv("GH_TOKEN"):
            return True
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True, text=True
        )
        return result.returncode == 0
    
    def setup(self) -> bool:
        """Validate gh CLI is available and authenticated."""
        self._org = os.getenv("GITHUB_ORG", "")
        
        # Check if gh CLI is installed
        result = subprocess.run(["which", "gh"], capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  ✗ GitHub: gh CLI not installed")
            return False
        
        # Check auth status
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            print(f"  ✗ GitHub: Not authenticated (run 'gh auth login' or set GH_TOKEN)")
            return False
        
        org_msg = f" (org: {self._org})" if self._org else ""
        print(f"  ✓ GitHub: Authenticated{org_msg}")
        return True
    
    async def download(
        self,
        output_dir: Path,
        state: dict,
        state_manager: "StateManager | None" = None,
    ) -> tuple[dict, ConnectorResult]:
        """Fetch repos and save as markdown."""
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Check if cache is still valid
        last_updated = state.get("last_updated")
        if last_updated:
            last_dt = datetime.fromisoformat(last_updated)
            if datetime.now() - last_dt < timedelta(hours=CACHE_TTL_HOURS):
                return state, ConnectorResult(
                    success=True,
                    items_skipped=len(state.get("repos", [])),
                    message="Cache still valid"
                )
        
        repos = _fetch_repos(self._org)
        print(f"  📦 GitHub: Found {len(repos)} repositories")
        
        if not repos:
            return state, ConnectorResult(success=True, message="No repos found")
        
        # Write markdown summary
        md_content = _format_repos_markdown(repos, self._org)
        md_path = output_dir / "github_repos.md"
        md_path.write_text(md_content)
        
        new_state = {
            "repos": [asdict(r) for r in repos],
            "last_updated": datetime.now().isoformat(),
        }
        
        if state_manager:
            await state_manager.update_item(self.name, "repos", new_state)
        
        print(f"  ✓ GitHub: {len(repos)} repos synced")
        return new_state, ConnectorResult(
            success=True,
            items_synced=len(repos),
        )


def _fetch_readme(repo: str) -> str:
    """Fetch and summarize a repo's README."""
    result = subprocess.run(
        ["gh", "api", f"repos/{repo}/readme", "--jq", ".content"],
        capture_output=True, text=True, timeout=15
    )
    if result.returncode != 0:
        return "_No README available_"
    
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


def _time_ago(date_str: str) -> str:
    """Convert ISO date to human-readable 'time ago' string."""
    if not date_str:
        return "unknown"
    date_str = date_str.replace("Z", "+00:00")
    dt = datetime.fromisoformat(date_str)
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


def _format_repos_markdown(repos: list[RepoInfo], org: str = "") -> str:
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
        lines.append("| Property | Value |")
        lines.append("|----------|-------|")
        lines.append(f"| **Default Branch** | `{repo.default_branch}` |")
        lines.append(f"| **Last Push** | {time_ago} |")
        lines.append(f"| **URL** | {repo.url} |")
        lines.append("")
        
        if repo.description:
            lines.append(f"**Description:** {repo.description}")
            lines.append("")
        
        lines.append("**README Summary:**")
        lines.append(f"> {repo.readme_summary}")
        lines.append("")
        lines.append("---")
        lines.append("")
    
    return "\n".join(lines)
