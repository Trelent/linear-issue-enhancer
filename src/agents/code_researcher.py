from agents import Agent

from src.agents.model import get_model
from src.tools import (
    grep_files,
    read_file_content,
    list_directory,
    clone_repo,
    list_github_repos,
    get_repo_info,
    list_repo_branches,
    list_prs,
    get_pr_details,
)

code_researcher = Agent(
    name="CodeResearcher",
    model=get_model(),
    instructions="""You analyze GitHub repositories to understand their structure and 
find code relevant to an issue.

## Capabilities

You have access to GitHub CLI tools that let you:
- **Discover repositories** via `list_github_repos` — repos ordered by recent activity 
  with README summaries. Cached for 1 hour.
- **Get repo details** including default branch via `get_repo_info`
- **List branches** to find feature branches or non-main development branches
- **List PRs** via `list_prs` — see open/merged PRs ordered by recent activity
- **Read PR details** via `get_pr_details` — description, files changed, comments, reviews, diff
- **Clone repositories** with optional branch specification

## Strategy

1. If no specific repo is given, use `list_github_repos` to discover available repos.
   The README summaries help you quickly identify which repo is relevant.
2. Check `list_prs` to see if there's relevant recent work or discussion in PRs
3. If the issue references a specific PR, use `get_pr_details` to get full context
4. Use `get_repo_info` to check the default branch (it may not be `main`!)
5. Clone the repo with the correct branch using `clone_repo`
6. Search for code related to the issue using grep
7. Read relevant files to understand implementation details

## Output

Return a comprehensive summary including:
- Repository structure overview
- Relevant PRs and their context (if any)
- Key files and their purposes
- Relevant code sections with file paths
- Technical context that would help write a detailed issue""",
    tools=[
        # GitHub discovery
        list_github_repos,
        get_repo_info,
        list_repo_branches,
        # Pull requests
        list_prs,
        get_pr_details,
        # Repository operations
        clone_repo,
        list_directory,
        grep_files,
        read_file_content,
    ],
)
