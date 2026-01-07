from agents import Agent

from src.agents.model import get_model_config
from src.tools import (
    grep_files,
    read_file_content,
    list_directory,
    clone_repo,
    list_cloned_repos,
    list_github_repos,
    get_repo_info,
    list_repo_branches,
    list_prs,
    get_pr_details,
)

CODE_RESEARCHER_INSTRUCTIONS = """You analyze GitHub repositories to understand their structure and 
find code relevant to an issue. You can work with MULTIPLE repositories simultaneously.

## Capabilities

You have access to GitHub CLI tools that let you:
- **Discover repositories** via `list_github_repos` — repos ordered by recent activity 
  with README summaries. Cached for 1 hour.
- **Get repo details** including default branch via `get_repo_info`
- **List branches** to find feature branches or non-main development branches
- **List PRs** via `list_prs` — see open/merged PRs ordered by recent activity
- **Read PR details** via `get_pr_details` — description, files changed, comments, reviews, diff
- **Clone repositories** — each repo gets its own directory
- **List cloned repos** via `list_cloned_repos` — see all repos you've cloned and their paths

## Multi-Repository Support

You can clone and analyze MULTIPLE repositories:
- Each `clone_repo` call creates a unique directory for that repo
- Use `list_cloned_repos` to see all cloned repos and their paths
- File tools (`grep_files`, `list_directory`, `read_file_content`) work on any path
- Cross-reference code between repos as needed

## Strategy

1. If no specific repo is given, use `list_github_repos` to discover available repos.
   The README summaries help you quickly identify which repos are relevant.
2. **Identify all relevant repos** — issues often span multiple repos (frontend + backend, 
   shared libs, infrastructure, etc.)
3. Check `list_prs` on each relevant repo to see recent work or discussion
4. If the issue references a specific PR, use `get_pr_details` to get full context
5. Use `get_repo_info` to check the default branch (it may not be `main`!)
6. Clone ALL relevant repos using `clone_repo` (each gets its own directory)
7. Use `list_cloned_repos` to see paths, then search for code across all repos
8. Read relevant files to understand implementation details

## Output

Return a comprehensive summary including:
- **Repositories**: ALL repositories analyzed, each in `owner/repo` format
- Structure overview of each relevant repo
- Relevant PRs and their context (if any)
- Key files and their purposes (with full paths)
- Relevant code sections across repos
- How the repos relate to each other (if multiple)
- Technical context that would help write a detailed issue

IMPORTANT: List ALL analyzed repositories at the start:
**Repositories:**
- `owner/repo1`
- `owner/repo2`
- ..."""

CODE_RESEARCHER_TOOLS = [
    # GitHub discovery
    list_github_repos,
    get_repo_info,
    list_repo_branches,
    # Pull requests
    list_prs,
    get_pr_details,
    # Repository operations
    clone_repo,
    list_cloned_repos,
    list_directory,
    grep_files,
    read_file_content,
]


def create_code_researcher(model_shorthand: str | None = None) -> Agent:
    """Create a code researcher agent with the specified model."""
    config = get_model_config(model_shorthand)
    return Agent(
        name="CodeResearcher",
        model=config.model,
        model_settings=config.model_settings,
        instructions=CODE_RESEARCHER_INSTRUCTIONS,
        tools=CODE_RESEARCHER_TOOLS,
    )


# Default instance for backwards compatibility
code_researcher = create_code_researcher()
