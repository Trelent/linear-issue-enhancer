# Linear Enhancer

AI-powered issue enhancement from context across Slack, Google Drive, and GitHub.

## Usage

### API Mode (Linear Webhook)

Run as an API server that automatically enhances Linear issues when they're created:

```bash
uv run python -m src.main serve
```

When you create an issue in Linear with just a title (or minimal description), the API:
1. Researches context from Slack and Google Drive
2. Analyzes relevant GitHub repositories
3. Updates the issue with a comprehensive description

#### Retry Enhancement

If you're not happy with the result, comment `/retry` on the issue to re-run the enhancement:

```
/retry please focus more on the authentication flow
```

You can also specify a model: `/retry [model=opus] try again with more detail`

### CLI Mode

Create an issue manually:

```bash
uv run python -m src.main issue \
  -p "Fix the authentication timeout issue" \
  -r Trelent/backend
```

| Flag | Description |
|------|-------------|
| `-p, --prompt` | Issue description (required) |
| `-r, --repo` | GitHub repo (`owner/repo`). Omit to auto-discover. |
| `-b, --branch` | Branch to analyze (default: repo's default) |

### Sync Context

Pull latest Slack messages and Google Drive docs:

```bash
uv run python -m src.main sync
```

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
| `LINEAR_API_KEY` | Linear API key (for API mode) |

### GitHub CLI

```bash
brew install gh
gh auth login
```

### Linear Webhook Setup

1. Get a Linear API key from **Settings → API → Personal API keys**
2. Add to `.env`: `LINEAR_API_KEY=lin_api_...`
3. Start the server: `uv run python -m src.main serve`
4. Expose via ngrok or deploy: `ngrok http 8000`
5. In Linear: **Settings → API → Webhooks → New webhook**
   - URL: `https://your-domain.ngrok.io/webhook/linear`
   - Data change: `Issue` → `Create` (for auto-enhancement)
   - Data change: `Comment` → `Create` (for `/retry` command)

### Optional: Google Drive

Set `GDRIVE_CREDS` to a service account JSON path.

### Slack Token

1. Create app at [api.slack.com/apps](https://api.slack.com/apps)
2. Add **User Token Scopes**: `channels:history`, `channels:read`, `groups:history`, `groups:read`, `users:read`
3. Install and copy the User OAuth Token

## How It Works

1. **Context Researcher** — searches synced Slack/GDrive for relevant context
2. **Code Researcher** — discovers repos via `gh`, analyzes code and PRs
3. **Issue Writer** — synthesizes into a comprehensive issue description

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/webhook/linear` | POST | Linear webhook receiver |

## Deployment

### Fly.io (Recommended)

```bash
# Install Fly CLI
brew install flyctl
fly auth login

# Deploy
./scripts/deploy-fly.sh
```

This sets up:
- Persistent `/data` volume for synced content
- Auto-scaling with minimum 1 instance
- HTTPS endpoint for Linear webhooks

### Scheduled Sync

After deploying, set up a cron job for regular syncs:

```bash
fly machine run . --schedule "*/30 * * * *" \
  -e DOCS_DIR=/data \
  --command "uv run python -m src.main sync"
```

### Environment Variables

Set these secrets in Fly.io:

```bash
fly secrets set ANTHROPIC_API_KEY=sk-ant-...
fly secrets set LINEAR_API_KEY=lin_api_...
fly secrets set SLACK_TOKEN=xoxp-...
fly secrets set GH_TOKEN=ghp_...  # For GitHub CLI auth

# Google Drive (base64-encode the service account JSON)
fly secrets set GDRIVE_CREDS_BASE64=$(cat credentials/gdrive-service-account.json | base64)
```

## Architecture

```
src/
├── main.py              # CLI entrypoint
├── api.py               # FastAPI webhook server
├── linear.py            # Linear API client
├── tools.py             # Agent tools
├── tracing.py           # Real-time logging
├── github_cache.py      # Repo caching
├── agents/              # Claude agents
└── sync/                # Slack and GDrive sync
```
