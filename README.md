# Linear Enhancer

AI-powered issue enhancement from context across Slack, Google Drive, Gmail, and GitHub.

## Usage

### API Mode (Linear Webhook)

Run as an API server that automatically enhances Linear issues when they're created:

```bash
uv run python -m src.main serve
```

When you create an issue in Linear with just a title (or minimal description), the API:
1. Researches context from Slack, Google Drive, and Gmail
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

Pull latest data from all enabled connectors:

```bash
uv run python -m src.main sync
```

Or sync specific connectors only:

```bash
uv run python -m src.main sync --connectors gmail,slack
```

## Setup

```bash
uv sync
cp env.example .env
```

### Required

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Anthropic API key for Claude |
| `LINEAR_API_KEY` | Linear API key (for API mode) |

### GitHub CLI (Required for Code Analysis)

The code researcher uses GitHub CLI to discover, clone, and analyze repositories.

```bash
# Local development
brew install gh
gh auth login

# Deployment (set token)
GH_TOKEN=ghp_your-token
```

Create a token at [github.com/settings/tokens](https://github.com/settings/tokens) with `repo` scope.

### Linear Webhook Setup

1. Get a Linear API key from **Settings → API → Personal API keys**
2. Add to `.env`: `LINEAR_API_KEY=lin_api_...`
3. Start the server: `uv run python -m src.main serve`
4. Expose via ngrok or deploy: `ngrok http 8000`
5. In Linear: **Settings → API → Webhooks → New webhook**
   - URL: `https://your-domain.ngrok.io/webhook/linear`
   - Data change: `Issue` → `Create` (for auto-enhancement)
   - Data change: `Comment` → `Create` (for `/retry` command)

---

## Data Connectors

Linear Enhancer uses a modular connector system. Each connector is **auto-enabled when its environment variable is set**. Check connector status with:

```bash
uv run python -c "from src.sync import print_connector_status; print_connector_status()"
```

### Slack

Syncs channel messages, DMs, and threads to markdown files.

**Enable:** Set `SLACK_TOKEN`

**Setup:**
1. Create a Slack app at [api.slack.com/apps](https://api.slack.com/apps)
2. Go to **OAuth & Permissions** → **User Token Scopes** and add:
   - `channels:history`, `channels:read` (public channels)
   - `groups:history`, `groups:read` (private channels)
   - `im:history`, `im:read` (direct messages)
   - `mpim:history`, `mpim:read` (group DMs)
   - `users:read`, `users:read.email` (user info)
3. Install to your workspace
4. Copy the **User OAuth Token** (`xoxp-...`) to your `.env`:
   ```
   SLACK_TOKEN=xoxp-your-token
   ```

> **Note:** User tokens (xoxp-) access all channels you're in. Bot tokens (xoxb-) require adding the bot to each channel.

---

### Google Drive

Syncs Google Docs and Sheets to markdown files.

**Enable:** Set `GDRIVE_CREDS` (file path) or `GDRIVE_CREDS_BASE64` (for deployment)

**Setup (Service Account):**
1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project (or select existing)
3. Enable **Google Drive API** and **Google Sheets API**
4. Go to **IAM & Admin → Service Accounts** → Create Service Account
5. Create a key (JSON) and download it
6. Share your Google Drive folders with the service account email
7. Add to `.env`:
   ```
   GDRIVE_CREDS=./credentials/gdrive-service-account.json
   ```

**For deployment** (base64-encode the JSON):
```bash
GDRIVE_CREDS_BASE64=$(cat credentials/gdrive-service-account.json | base64)
```

---

### Gmail

Syncs emails from allowed senders to markdown files. Filters out spam, trash, promotions, and social.

**Enable:** Set `GMAIL_ENABLED=true` (requires Google credentials from GDrive setup)

**⚠️ Gmail is more complex to set up than other connectors.**

**How it works:**
- Uses the same Google credentials as GDrive
- Only syncs emails from an **allow-list** of senders
- Allow-list auto-includes: all Slack users' emails + `INTERNAL_DOMAINS`

**Setup (OAuth - Personal Gmail):**

If you used OAuth (not a service account) for GDrive, Gmail should work automatically. Just enable it:
```
GMAIL_ENABLED=true
```

**Setup (Service Account - Google Workspace):**

Service accounts can only access Gmail via **domain-wide delegation**:

1. In Google Cloud Console, go to your service account
2. Enable **Domain-wide Delegation**
3. Copy the **Client ID**
4. In [Google Workspace Admin](https://admin.google.com/) → **Security** → **Access and data control** → **API Controls** → **Domain-wide Delegation**
5. Add the Client ID with scope: `https://www.googleapis.com/auth/gmail.readonly`
6. Specify which user to impersonate:
   ```bash
   GMAIL_USER_EMAIL=your-email@yourcompany.com
   ```

> **Note:** Domain-wide delegation requires Google Workspace (not personal Gmail). For personal Gmail, use OAuth credentials instead.

**Allow-list configuration:**
```bash
# Emails from these addresses/domains are synced (comma-separated)
GMAIL_ALLOWED_SENDERS=partner@vendor.com,@trusted-partner.io

# Internal domains are always allowed
INTERNAL_DOMAINS=yourcompany.com

# Slack users' emails are auto-included from the Slack sync
```

---

## How It Works

1. **Context Researcher** — searches synced Slack/GDrive/Gmail for relevant context
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

### Environment Variables (Fly.io)

```bash
# Required
fly secrets set ANTHROPIC_API_KEY=sk-ant-...
fly secrets set LINEAR_API_KEY=lin_api_...

# Connectors (set the ones you want to enable)
fly secrets set SLACK_TOKEN=xoxp-...
fly secrets set GH_TOKEN=ghp_...
fly secrets set GDRIVE_CREDS_BASE64=$(cat credentials/gdrive-service-account.json | base64)

# Optional: Gmail (uses same creds as GDrive)
fly secrets set GMAIL_ENABLED=true
fly secrets set GMAIL_ALLOWED_SENDERS=partner@example.com

# Optional: Internal domains for tagging
fly secrets set INTERNAL_DOMAINS=yourcompany.com
```

## Architecture

```
src/
├── main.py              # CLI entrypoint
├── api.py               # FastAPI webhook server
├── linear.py            # Linear API client
├── tools.py             # Agent tools
├── tracing.py           # Real-time logging
├── github_cache.py      # Repo caching (for tools)
├── agents/              # Claude agents
└── sync/
    ├── connector.py     # Base connector interface
    ├── registry.py      # Connector discovery
    └── connectors/      # Slack, GDrive, GitHub, Gmail
```
