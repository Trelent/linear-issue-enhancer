"""Gmail connector - syncs emails from allowed senders to markdown.

Features:
- Filters out spam, trash, promotions, social
- Allow-list filtering by sender email/domain
- Auto-includes Slack users' emails in allow-list
- Incremental sync based on message date
"""

import json
import asyncio
import base64
import os
import re
from pathlib import Path
from datetime import datetime
from email.utils import parseaddr
from functools import partial
from typing import TYPE_CHECKING

from google.oauth2.credentials import Credentials
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from src.sync.connector import Connector, ConnectorResult
from src.sync.config import is_internal_email

if TYPE_CHECKING:
    from src.sync import StateManager


SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# Labels to exclude
EXCLUDED_LABELS = {"SPAM", "TRASH", "CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_UPDATES"}

# Rate limiting - keep concurrency low to avoid thread safety issues
GMAIL_RATE_LIMIT = 5
GMAIL_CONCURRENT_FETCHES = 1  # Sequential to avoid thread safety issues with Google API client


class GmailConnector(Connector):
    """Syncs Gmail messages from allowed senders to markdown files.
    
    Allow-list sources (combined):
    1. GMAIL_ALLOWED_SENDERS env var (comma-separated emails or @domains)
    2. All Slack users' emails (auto-loaded from slack_users.json)
    3. INTERNAL_DOMAINS emails are always allowed
    """
    
    name = "gmail"
    env_key = "GMAIL_ENABLED"
    
    def __init__(self):
        super().__init__()
        self._creds: Credentials | None = None
        self._allowed_emails: set[str] = set()
        self._allowed_domains: set[str] = set()
        self._user_email: str = ""
    
    @property
    def enabled(self) -> bool:
        # Requires explicit enable + Google credentials
        if not os.getenv("GMAIL_ENABLED"):
            return False
        return bool(os.getenv("GDRIVE_CREDS") or os.getenv("GDRIVE_CREDS_BASE64"))
    
    def setup(self) -> bool:
        """Load credentials, build allow-list, and test connection."""
        self._creds = _load_credentials()
        if not self._creds:
            return False
        
        # Build allow-list from env var
        allowed_raw = os.getenv("GMAIL_ALLOWED_SENDERS", "")
        for item in allowed_raw.split(","):
            item = item.strip().lower()
            if not item:
                continue
            if item.startswith("@"):
                self._allowed_domains.add(item[1:])
            else:
                self._allowed_emails.add(item)
        
        # Add internal domains
        from src.sync.config import INTERNAL_DOMAINS
        self._allowed_domains.update(d.lower() for d in INTERNAL_DOMAINS)
        
        # Test connection and get user email
        try:
            service = build("gmail", "v1", credentials=self._creds)
            profile = service.users().getProfile(userId="me").execute()
            self._user_email = profile.get("emailAddress", "unknown")
            print(f"  ✓ Gmail: Connected as {self._user_email}")
            return True
        except HttpError as e:
            print(f"  ✗ Gmail: Connection failed - {e}")
            return False
    
    def _load_slack_emails(self, data_dir: Path) -> set[str]:
        """Load email addresses from Slack user cache."""
        slack_cache = data_dir / "slack" / "slack_users.json"
        if not slack_cache.exists():
            return set()
        
        try:
            users = json.loads(slack_cache.read_text())
            return {
                u.get("email", "").lower()
                for u in users.values()
                if u.get("email")
            }
        except Exception:
            return set()
    
    def _is_sender_allowed(self, sender_email: str) -> bool:
        """Check if sender is in allow-list."""
        email = sender_email.lower()
        
        # Check exact email match
        if email in self._allowed_emails:
            return True
        
        # Check domain match
        if "@" in email:
            domain = email.split("@")[1]
            if domain in self._allowed_domains:
                return True
        
        return False
    
    async def download(
        self,
        output_dir: Path,
        state: dict,
        state_manager: "StateManager | None" = None,
    ) -> tuple[dict, ConnectorResult]:
        """Sync Gmail messages to markdown files."""
        output_dir.mkdir(parents=True, exist_ok=True)
        
        if not self._creds:
            self._creds = _load_credentials()
            if not self._creds:
                return state, ConnectorResult(success=False, message="No credentials")
        
        # Load Slack emails into allow-list
        data_dir = output_dir.parent  # Go up from gmail/ to data/
        slack_emails = self._load_slack_emails(data_dir)
        self._allowed_emails.update(slack_emails)
        
        allow_count = len(self._allowed_emails) + len(self._allowed_domains)
        print(f"  📧 Gmail: Allow-list has {len(self._allowed_emails)} emails, {len(self._allowed_domains)} domains")
        
        if allow_count == 0:
            print(f"  ⚠ Gmail: No allowed senders configured, skipping")
            return state, ConnectorResult(success=True, message="No allowed senders")
        
        service = build("gmail", "v1", credentials=self._creds)
        
        # Get last sync timestamp
        last_sync_ts = state.get("last_sync_ts", "0")
        last_sync_date = state.get("last_sync_date", "")
        
        # Build query: exclude spam/trash/promotions, only after last sync
        query_parts = ["-label:spam", "-label:trash", "-category:promotions", "-category:social"]
        if last_sync_date:
            query_parts.append(f"after:{last_sync_date}")
        query = " ".join(query_parts)
        
        # Fetch message list
        messages = await _list_messages(service, query)
        print(f"  📧 Gmail: Found {len(messages)} messages since last sync")
        
        if not messages:
            return state, ConnectorResult(success=True, items_skipped=0, message="No new messages")
        
        # Fetch full message details and filter by allow-list
        # Process sequentially to avoid thread safety issues with Google API client
        print(f"  📧 Gmail: Fetching message details...")
        rate_limiter = RateLimiter(GMAIL_RATE_LIMIT)
        
        fetched = []
        for i, msg in enumerate(messages):
            if i > 0 and i % 50 == 0:
                print(f"  📧 Gmail: Processed {i}/{len(messages)} messages...")
            msg_data = await _fetch_message(service, msg["id"], rate_limiter)
            fetched.append(msg_data)
        
        # Filter by allow-list and process
        allowed_messages = []
        filtered_count = 0
        
        for msg in fetched:
            if not msg:
                continue
            
            sender = msg.get("from", "")
            _, sender_email = parseaddr(sender)
            
            if not self._is_sender_allowed(sender_email):
                filtered_count += 1
                continue
            
            allowed_messages.append(msg)
        
        # Sort by date and write to markdown
        allowed_messages.sort(key=lambda m: m.get("internal_date", 0))
        
        # Write to a single file per day or append to existing
        messages_by_date: dict[str, list] = {}
        for msg in allowed_messages:
            date_str = msg.get("date_str", "unknown")[:10]  # YYYY-MM-DD
            if date_str not in messages_by_date:
                messages_by_date[date_str] = []
            messages_by_date[date_str].append(msg)
        
        for date_str, day_messages in messages_by_date.items():
            md_path = output_dir / f"emails_{date_str}.md"
            _append_messages_to_md(md_path, day_messages)
        
        # Update state
        new_state = {
            "last_sync_ts": datetime.now().timestamp(),
            "last_sync_date": datetime.now().strftime("%Y/%m/%d"),
            "message_count": state.get("message_count", 0) + len(allowed_messages),
        }
        
        if state_manager:
            await state_manager.update_item(self.name, "sync", new_state)
        
        print(f"  ✓ Gmail: {len(allowed_messages)} emails synced, {filtered_count} filtered (not in allow-list)")
        
        return new_state, ConnectorResult(
            success=True,
            items_synced=len(allowed_messages),
            items_skipped=filtered_count,
        )


class RateLimiter:
    """Simple token bucket rate limiter."""
    
    def __init__(self, rate_per_second: float):
        self.rate = rate_per_second
        self.tokens = rate_per_second
        self.last_update = asyncio.get_event_loop().time()
        self.lock = asyncio.Lock()
    
    async def acquire(self):
        async with self.lock:
            now = asyncio.get_event_loop().time()
            elapsed = now - self.last_update
            self.tokens = min(self.rate, self.tokens + elapsed * self.rate)
            self.last_update = now
            
            if self.tokens < 1:
                wait_time = (1 - self.tokens) / self.rate
                await asyncio.sleep(wait_time)
                self.tokens = 0
            else:
                self.tokens -= 1


async def _run_in_executor(func, *args, **kwargs):
    """Run a blocking function in a thread pool executor."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, partial(func, *args, **kwargs))


async def _list_messages(service, query: str, max_results: int = 500) -> list[dict]:
    """List messages matching query."""
    try:
        results = await _run_in_executor(
            lambda: service.users().messages().list(
                userId="me",
                q=query,
                maxResults=max_results,
            ).execute()
        )
        return results.get("messages", [])
    except HttpError as e:
        print(f"  ✗ Gmail: Error listing messages - {e}")
        return []


async def _fetch_message(service, msg_id: str, rate_limiter: RateLimiter) -> dict | None:
    """Fetch full message details."""
    await rate_limiter.acquire()
    
    try:
        msg = await _run_in_executor(
            lambda: service.users().messages().get(
                userId="me",
                id=msg_id,
                format="full",
            ).execute()
        )
        
        headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
        
        # Parse date
        internal_date = int(msg.get("internalDate", 0)) / 1000
        date_str = datetime.fromtimestamp(internal_date).strftime("%Y-%m-%d %H:%M") if internal_date else ""
        
        # Extract body
        body = _extract_body(msg.get("payload", {}))
        
        return {
            "id": msg_id,
            "from": headers.get("from", ""),
            "to": headers.get("to", ""),
            "subject": headers.get("subject", "(no subject)"),
            "date": headers.get("date", ""),
            "date_str": date_str,
            "internal_date": internal_date,
            "body": body,
            "snippet": msg.get("snippet", ""),
        }
    except HttpError as e:
        print(f"  ✗ Gmail: Error fetching message {msg_id} - {e}")
        return None


def _extract_body(payload: dict) -> str:
    """Extract text body from message payload."""
    # Try to get plain text part
    if payload.get("mimeType") == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
    
    # Check parts recursively
    for part in payload.get("parts", []):
        if part.get("mimeType") == "text/plain":
            data = part.get("body", {}).get("data", "")
            if data:
                return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
        
        # Recurse into nested parts
        nested = _extract_body(part)
        if nested:
            return nested
    
    return ""


def _append_messages_to_md(path: Path, messages: list[dict]):
    """Append messages to a markdown file."""
    existing = path.read_text() if path.exists() else f"# Emails - {path.stem.replace('emails_', '')}\n\n"
    
    lines = []
    for msg in messages:
        sender = msg["from"]
        _, sender_email = parseaddr(sender)
        sender_name = sender.replace(f"<{sender_email}>", "").strip().strip('"')
        
        tag = "internal" if is_internal_email(sender_email) else "external"
        
        lines.append("---")
        lines.append(f"## {msg['subject']}")
        lines.append("")
        lines.append(f"**From:** {sender_name} <{sender_email}> [{tag}]")
        lines.append(f"**To:** {msg['to']}")
        lines.append(f"**Date:** {msg['date_str']}")
        lines.append("")
        
        # Clean up body
        body = msg["body"].strip()
        if not body:
            body = msg["snippet"]
        
        # Truncate very long emails
        if len(body) > 5000:
            body = body[:5000] + "\n\n... (truncated)"
        
        lines.append(body)
        lines.append("")
    
    path.write_text(existing + "\n".join(lines))


def _load_credentials() -> Credentials | None:
    """Load credentials from file path or GDRIVE_CREDS_BASE64 env var."""
    # Gmail uses same creds as GDrive but different scope
    user_email = os.getenv("GMAIL_USER_EMAIL")
    
    creds_base64 = os.getenv("GDRIVE_CREDS_BASE64")
    if creds_base64:
        try:
            creds_json = base64.b64decode(creds_base64).decode("utf-8")
            creds_data = json.loads(creds_json)
            if creds_data.get("type") == "service_account":
                creds = service_account.Credentials.from_service_account_info(creds_data, scopes=SCOPES)
                if user_email:
                    return creds.with_subject(user_email)
                print(f"  ✗ Gmail: Service account requires GMAIL_USER_EMAIL to impersonate a user")
                return None
            return Credentials.from_authorized_user_info(creds_data, SCOPES)
        except Exception as e:
            print(f"  ✗ Gmail: Failed to load credentials from GDRIVE_CREDS_BASE64: {e}")
            return None
    
    creds_path = os.getenv("GDRIVE_CREDS")
    if not creds_path:
        return None
    
    path = Path(creds_path)
    if not path.exists():
        print(f"  ✗ Gmail: Credentials file not found: {creds_path}")
        return None
    
    try:
        creds_data = json.loads(path.read_text())
        if creds_data.get("type") == "service_account":
            creds = service_account.Credentials.from_service_account_file(creds_path, scopes=SCOPES)
            if user_email:
                return creds.with_subject(user_email)
            print(f"  ✗ Gmail: Service account requires GMAIL_USER_EMAIL to impersonate a user")
            return None
        return Credentials.from_authorized_user_file(creds_path, SCOPES)
    except Exception as e:
        print(f"  ✗ Gmail: Failed to load credentials: {e}")
        return None
