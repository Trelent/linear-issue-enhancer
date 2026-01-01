# Linear Enhancer

AI-powered issue creation from context across Slack, Google Drive, and GitHub.

## Usage

### Create an Issue

```bash
# Specify repo and branch
uv run python -m src.main issue \
  -p "Fix the authentication timeout issue" \
  -r Trelent/backend \
  -b develop \
  -d ./data

# Let the agent discover the relevant repo from context
uv run python -m src.main issue \
  -p "Fix the rate limiting bug we discussed in Slack" \
  -d ./data
```

| Flag | Description |
|------|-------------|
| `-p, --prompt` | Issue description (required) |
| `-r, --repo` | GitHub repo (`owner/repo`). Omit to auto-discover. |
| `-b, --branch` | Branch to analyze (default: repo's default) |
| `-d, --docs` | Directory with synced context (required) |

### Sync Context

Pull latest Slack messages and Google Drive docs:

```bash
uv run python -m src.main sync -d ./data
```

Syncs incrementally — only fetches new messages and modified docs.

## Setup

```bash
uv sync
cp env.example .env
```

### Required

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `SLACK_TOKEN` | Slack user token (`xoxp-...`) |

### GitHub CLI

```bash
brew install gh
gh auth login
```

Enables repo discovery, branch listing, and README fetching.

### Optional: Google Drive

Set `GDRIVE_CREDS` to a service account JSON path. See [Google Cloud Console](https://console.cloud.google.com/) to create one.

### Slack Token

1. Create app at [api.slack.com/apps](https://api.slack.com/apps)
2. Add **User Token Scopes**: `channels:history`, `channels:read`, `groups:history`, `groups:read`, `users:read`
3. Install and copy the User OAuth Token

## How It Works

1. **Context Researcher** — searches synced Slack/GDrive markdown for relevant context
2. **Code Researcher** — discovers repos via `gh`, fetches README summaries, clones and analyzes code
3. **Issue Writer** — synthesizes everything into a structured Linear issue

Repo discovery results are cached for 1 hour in `data/github_cache.json`.

## Architecture

```
src/
├── main.py              # CLI and orchestration
├── tools.py             # Agent tools (grep, read, clone, GitHub discovery)
├── github_cache.py      # Repo caching with README fetching
├── agents/              # Claude agents
└── sync/                # Slack and GDrive sync
```
