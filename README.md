# Linear Enhancer

AI-powered issue creation from context across Slack, Google Drive, and GitHub. Uses Claude to research relevant context and write comprehensive Linear issues.

## Setup

```bash
# Install dependencies
uv sync

# Copy environment template
cp env.example .env
```

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes | Anthropic API key for Claude |
| `SLACK_TOKEN` | Yes | Slack user token (`xoxp-...`) or bot token (`xoxb-...`) |
| `GDRIVE_CREDS` | No | Path to Google Drive credentials JSON |
| `INTERNAL_DOMAINS` | No | Comma-separated email domains for internal user tagging (default: `trelent.com`) |

### Slack Token Setup

User tokens are recommended (access all channels you're in without adding a bot):

1. Go to [api.slack.com/apps](https://api.slack.com/apps)
2. Create a new app → "From scratch"
3. Go to **OAuth & Permissions**
4. Add these **User Token Scopes**:
   - `channels:history`, `channels:read`
   - `groups:history`, `groups:read`
   - `users:read`, `users:read.email`
5. Install to workspace
6. Copy the **User OAuth Token** (`xoxp-...`)

### Google Drive Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create/select a project
3. Enable **Google Drive API** and **Google Sheets API**
4. Create a **Service Account**:
   - APIs & Services → Credentials → Create Credentials → Service Account
   - Download the JSON key
5. Share folders/drives with the service account email

## Usage

### Sync Data

Pull latest messages from Slack and docs from Google Drive:

```bash
uv run python -m src.main sync --docs ./data
```

Options:
- `--docs, -d` — Directory to store synced markdown files (required)
- `--slack-token` — Override Slack token (or use `SLACK_TOKEN` env var)
- `--gdrive-creds` — Override GDrive credentials path (or use `GDRIVE_CREDS` env var)

**What gets synced:**
- **Slack**: All channels you're a member of → `data/slack/{channel}.md`
- **Google Drive**: All Docs and Sheets in shared drives → `data/gdrive/{doc}.md`

**Incremental sync:**
- Only fetches new Slack messages since last sync (tracked per-channel)
- Only re-downloads Google Docs if modified since last sync
- State stored in `data/sync_state.json`

### Create Issue

Generate a comprehensive Linear issue from all context sources:

```bash
uv run python -m src.main issue \
  --prompt "We need to fix the authentication timeout issue" \
  --repo https://github.com/org/repo \
  --docs ./data
```

Options:
- `--prompt, -p` — Issue description/request (required)
- `--repo, -r` — GitHub repository URL to analyze (required)
- `--docs, -d` — Directory with synced markdown context (required)
- `--sync-max-age` — Minutes before auto-resync (default: 30)

**What happens:**
1. Auto-syncs if data is older than `--sync-max-age`
2. **Context Researcher** agent searches Slack/GDrive markdown files for relevant context
3. **Code Researcher** agent clones the repo and analyzes relevant code
4. **Issue Writer** agent synthesizes everything into a structured Linear issue

## Data Format

### Slack Messages

```markdown
# #channel-name

---
### **John Smith** <john@company.com> [internal]
*2025-01-01 14:30*

Message text here...

<details><summary>📎 Thread replies</summary>

> **External User** <client@acme.com> [external] *2025-01-01 14:45*
>
> Reply text...

</details>
```

### Google Docs

```markdown
# Document Title

| Property | Value |
|----------|-------|
| Last Modified | 2025-01-01 10:00 |
| Owner | Jane Doe <jane@company.com> [internal] |
| Source | Google Drive |
| Doc ID | `abc123` |

---

Document content here...
```

### Google Sheets

Each sheet becomes a markdown table with formulas shown inline:

```markdown
## 📊 Sheet Name

| Column A | Column B |
| --- | --- |
| 100 | 200 `=A1*2` |
| Total | 300 `=SUM(B1:B2)` |
```

## Architecture

```
src/
├── main.py              # CLI and orchestration
├── tools.py             # Function tools (grep, read, clone)
├── agents/
│   ├── context_researcher.py  # Searches markdown files
│   ├── code_researcher.py     # Analyzes GitHub repos
│   └── issue_writer.py        # Writes Linear issues
└── sync/
    ├── __init__.py      # Sync orchestration
    ├── config.py        # Environment config
    ├── slack.py         # Slack API sync
    └── gdrive.py        # Google Drive API sync
```

## Development

```bash
# Run sync
uv run python -m src.main sync --docs ./data

# Run issue creation
uv run python -m src.main issue -p "Fix auth bug" -r https://github.com/org/repo -d ./data
```

