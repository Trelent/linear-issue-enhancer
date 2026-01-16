"""Slack connector - syncs channels and DMs to markdown.

Works with user tokens (xoxp-) or bot tokens (xoxb-).
User tokens are recommended as they access all channels you're in without adding a bot.
"""

import json
import asyncio
import os
from pathlib import Path
from datetime import datetime
from functools import partial

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from src.sync.connector import Connector, ConnectorResult
from src.sync.config import is_internal_email
from src.sync import StateManager


USER_CACHE_FILE = "slack_users.json"

# Slack rate limits
SLACK_RATE_LIMIT = 4.0
SLACK_BURST_CAPACITY = 10
SLACK_CONCURRENT_CHANNELS = 6


class SlackConnector(Connector):
    """Syncs Slack channels and DMs to markdown files."""
    
    name = "slack"
    env_key = "SLACK_TOKEN"
    
    def __init__(self):
        super().__init__()
        self._token: str = ""
        self._client: WebClient | None = None
    
    def setup(self) -> bool:
        """Load token and test Slack connection."""
        self._token = os.getenv("SLACK_TOKEN", "")
        if not self._token:
            print(f"  ✗ Slack: SLACK_TOKEN not set")
            return False
        
        self._client = WebClient(token=self._token)
        
        # Test the connection
        try:
            result = self._client.auth_test()
            team = result.get("team", "unknown")
            user = result.get("user", "unknown")
            print(f"  ✓ Slack: Connected as {user} to {team}")
            return True
        except SlackApiError as e:
            print(f"  ✗ Slack: Auth failed - {e.response['error']}")
            return False
    
    async def download(
        self,
        output_dir: Path,
        state: dict,
        state_manager: "StateManager | None" = None,
    ) -> tuple[dict, ConnectorResult]:
        """Sync Slack channels to markdown files."""
        output_dir.mkdir(parents=True, exist_ok=True)
        
        if not self._client:
            self._client = WebClient(token=self._token)
        
        users = _load_user_cache(output_dir)
        users_lock = asyncio.Lock()
        rate_limiter = RateLimiter(SLACK_RATE_LIMIT, SLACK_BURST_CAPACITY)
        
        channels = await _get_channels_async(self._client, rate_limiter, users, users_lock)
        dm_count = sum(1 for c in channels if c.get("is_im") or c.get("is_mpim"))
        channel_count = len(channels) - dm_count
        print(f"  📨 Slack: Found {channel_count} channels, {dm_count} DMs")
        
        semaphore = asyncio.Semaphore(SLACK_CONCURRENT_CHANNELS)
        print_lock = asyncio.Lock()
        progress = {"done": 0, "total": len(channels)}
        
        async def process_channel(channel: dict) -> tuple[str, dict, int, int, str | None]:
            async with semaphore:
                channel_id, channel_state, msg_count, reply_count, channel_name = await _sync_channel(
                    self._client, channel, state, users, users_lock, output_dir, rate_limiter
                )
                
                if state_manager:
                    await state_manager.update_item(self.name, channel_id, channel_state)
                
                async with print_lock:
                    progress["done"] += 1
                    prefix = "💬" if channel.get("is_im") or channel.get("is_mpim") else "#"
                    if msg_count > 0:
                        print(f"     [{progress['done']}/{progress['total']}] {prefix}{channel_name}: +{msg_count} msgs, +{reply_count} replies")
                    else:
                        print(f"     [{progress['done']}/{progress['total']}] {prefix}{channel_name}: (no new)")
                
                return channel_id, channel_state, msg_count, reply_count, channel_name
        
        results = await asyncio.gather(*[process_channel(ch) for ch in channels])
        
        new_state = {}
        synced_count = 0
        skipped_count = 0
        total_messages = 0
        
        for channel_id, channel_state, msg_count, reply_count, _ in results:
            new_state[channel_id] = channel_state
            if msg_count == 0:
                skipped_count += 1
            else:
                synced_count += 1
                total_messages += msg_count + reply_count
        
        _save_user_cache(output_dir, users)
        print(f"  ✓ Slack: {synced_count} channels updated, {skipped_count} unchanged, {total_messages} total messages")
        
        return new_state, ConnectorResult(
            success=True,
            items_synced=synced_count,
            items_skipped=skipped_count,
            message=f"{total_messages} messages"
        )


# --- Rate limiter and helpers (unchanged from original) ---

class RateLimiter:
    """Token bucket rate limiter with burst capacity."""
    
    def __init__(self, rate_per_second: float, burst_capacity: float = None):
        self.rate = rate_per_second
        self.max_tokens = burst_capacity or rate_per_second * 2
        self.tokens = self.max_tokens
        self.last_update = asyncio.get_event_loop().time()
        self.lock = asyncio.Lock()
    
    async def acquire(self):
        async with self.lock:
            now = asyncio.get_event_loop().time()
            elapsed = now - self.last_update
            self.tokens = min(self.max_tokens, self.tokens + elapsed * self.rate)
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


async def _call_with_retry(func, rate_limiter: RateLimiter, max_retries: int = 3):
    """Call a Slack API function with retry on rate limit."""
    for attempt in range(max_retries):
        await rate_limiter.acquire()
        try:
            return await _run_in_executor(func)
        except SlackApiError as e:
            if e.response.get("error") == "ratelimited":
                # Get retry-after header or use exponential backoff
                retry_after = int(e.response.headers.get("Retry-After", 2 ** attempt))
                print(f"  ⏳ Rate limited, waiting {retry_after}s...")
                await asyncio.sleep(retry_after)
                continue
            raise
    # Final attempt
    await rate_limiter.acquire()
    return await _run_in_executor(func)


def _get_conversation_name(channel: dict) -> str:
    """Get the display name for a channel or DM."""
    if channel.get("is_im"):
        return f"dm-{channel.get('_dm_user_name', channel.get('user', 'unknown'))}"
    if channel.get("is_mpim"):
        return channel.get("name", "group-dm").replace(" ", "-")
    return channel["name"]


async def _sync_channel(
    client: WebClient,
    channel: dict,
    state: dict,
    users: dict,
    users_lock: asyncio.Lock,
    output_dir: Path,
    rate_limiter: RateLimiter,
) -> tuple[str, dict, int, int, str | None]:
    """Sync a single channel/DM. Returns (channel_id, state_entry, msg_count, reply_count, channel_name)."""
    channel_id = channel["id"]
    channel_name = _get_conversation_name(channel)
    is_dm = channel.get("is_im") or channel.get("is_mpim")
    
    last_ts = state.get(channel_id, {}).get("last_ts", "0")
    messages, latest_ts = await _get_messages_with_threads_async(
        client, channel_id, users, users_lock, rate_limiter, oldest=last_ts
    )
    
    if not messages:
        return channel_id, state.get(channel_id, {"last_ts": "0", "name": channel_name}), 0, 0, channel_name
    
    md_path = output_dir / f"{channel_name}.md"
    await _run_in_executor(_append_messages_to_md, md_path, channel_name, messages, is_dm)
    
    reply_count = sum(len(m.get("replies", [])) for m in messages)
    return channel_id, {"last_ts": latest_ts, "name": channel_name}, len(messages), reply_count, channel_name


def _load_user_cache(output_dir: Path) -> dict:
    cache_path = output_dir / USER_CACHE_FILE
    if cache_path.exists():
        return json.loads(cache_path.read_text())
    return {}


def _save_user_cache(output_dir: Path, users: dict):
    cache_path = output_dir / USER_CACHE_FILE
    cache_path.write_text(json.dumps(users, indent=2))


async def _get_channels_async(
    client: WebClient, rate_limiter: RateLimiter, users: dict, users_lock: asyncio.Lock
) -> list:
    """Get list of channels and DMs the bot has access to."""
    try:
        result = await _call_with_retry(
            lambda: client.conversations_list(types="public_channel,private_channel,im,mpim", limit=999),
            rate_limiter,
        )
        conversations = result.get("channels", [])
        
        # Resolve DM user names sequentially to avoid rate limits
        for conv in conversations:
            if conv.get("is_im") and conv.get("user"):
                user_info = await _get_user_info_async(client, conv["user"], users, users_lock, rate_limiter)
                conv["_dm_user_name"] = user_info["name"]
        
        return conversations
    except SlackApiError as e:
        print(f"  ✗ Slack API error listing channels: {e}")
        return []


async def _get_user_info_async(
    client: WebClient, user_id: str, users: dict, users_lock: asyncio.Lock, rate_limiter: RateLimiter
) -> dict:
    """Get user info with caching."""
    async with users_lock:
        if user_id in users:
            return users[user_id]
    
    try:
        result = await _call_with_retry(lambda: client.users_info(user=user_id), rate_limiter)
        user = result.get("user", {})
        profile = user.get("profile", {})
        email = profile.get("email", "")
        
        info = {
            "name": profile.get("real_name") or user.get("name") or user_id,
            "email": email,
            "is_internal": is_internal_email(email),
        }
        
        async with users_lock:
            users[user_id] = info
        return info
    except SlackApiError:
        return {"name": user_id, "email": "", "is_internal": False}


async def _get_messages_with_threads_async(
    client: WebClient,
    channel_id: str,
    users: dict,
    users_lock: asyncio.Lock,
    rate_limiter: RateLimiter,
    oldest: str = "0",
) -> tuple[list, str]:
    """Get messages and their thread replies."""
    try:
        result = await _call_with_retry(
            lambda: client.conversations_history(channel=channel_id, oldest=oldest, limit=999),
            rate_limiter,
        )
        messages = result.get("messages", [])
        
        if not messages:
            return [], oldest
        
        # Fetch thread replies sequentially to avoid rate limits
        thread_replies = {}
        for msg in messages:
            if msg.get("thread_ts") == msg["ts"] and msg.get("reply_count", 0) > 0:
                replies = await _get_thread_replies_async(
                    client, channel_id, msg["ts"], users, users_lock, rate_limiter, oldest
                )
                thread_replies[msg["ts"]] = replies
        
        # Fetch user info for users not in cache (sequentially)
        user_ids = list(set(msg.get("user", "") for msg in messages if msg.get("user")))
        for uid in user_ids:
            if uid not in users:
                await _get_user_info_async(client, uid, users, users_lock, rate_limiter)
        
        enriched = []
        for msg in messages:
            user_id = msg.get("user", "")
            user_info = users.get(user_id, {"name": user_id, "email": "", "is_internal": False})
            
            enriched.append({
                "ts": msg["ts"],
                "text": msg.get("text", ""),
                "user": user_info,
                "replies": thread_replies.get(msg["ts"], []),
            })
        
        latest_ts = max(m["ts"] for m in messages)
        return enriched, latest_ts
    except SlackApiError as e:
        print(f"  ✗ Error fetching messages from channel {channel_id}: {e}")
        return [], oldest


async def _get_thread_replies_async(
    client: WebClient,
    channel_id: str,
    thread_ts: str,
    users: dict,
    users_lock: asyncio.Lock,
    rate_limiter: RateLimiter,
    oldest: str,
) -> list:
    """Get replies in a thread, excluding the parent message."""
    try:
        result = await _call_with_retry(
            lambda: client.conversations_replies(channel=channel_id, ts=thread_ts, oldest=oldest, limit=100),
            rate_limiter,
        )
        replies = result.get("messages", [])[1:]
        
        valid_replies = [r for r in replies if float(r["ts"]) > float(oldest)]
        
        # Fetch user info sequentially
        for r in valid_replies:
            uid = r.get("user", "")
            if uid and uid not in users:
                await _get_user_info_async(client, uid, users, users_lock, rate_limiter)
        
        enriched = []
        for reply in valid_replies:
            user_id = reply.get("user", "")
            user_info = users.get(user_id, {"name": user_id, "email": "", "is_internal": False})
            enriched.append({
                "ts": reply["ts"],
                "text": reply.get("text", ""),
                "user": user_info,
            })
        return enriched
    except SlackApiError:
        return []


def _format_user(user: dict) -> str:
    """Format user as 'Name <email> [internal/external]'."""
    parts = [f"**{user['name']}**"]
    if user["email"]:
        parts.append(f"<{user['email']}>")
    tag = "internal" if user["is_internal"] else "external"
    parts.append(f"[{tag}]")
    return " ".join(parts)


def _format_timestamp(ts: str) -> str:
    dt = datetime.fromtimestamp(float(ts))
    return dt.strftime("%Y-%m-%d %H:%M")


def _append_messages_to_md(path: Path, channel_name: str, messages: list, is_dm: bool = False):
    """Append messages to a markdown file with thread structure."""
    prefix = "💬 DM with" if is_dm else "#"
    existing = path.read_text() if path.exists() else f"# {prefix} {channel_name.replace('dm-', '') if is_dm else channel_name}\n\n"
    
    lines = []
    for msg in sorted(messages, key=lambda m: float(m["ts"])):
        user_str = _format_user(msg["user"])
        time_str = _format_timestamp(msg["ts"])
        
        lines.append(f"---\n### {user_str}\n*{time_str}*\n\n{msg['text']}\n")
        
        if msg["replies"]:
            lines.append("\n<details><summary>📎 Thread replies</summary>\n")
            for reply in msg["replies"]:
                reply_user = _format_user(reply["user"])
                reply_time = _format_timestamp(reply["ts"])
                lines.append(f"\n> {reply_user} *{reply_time}*\n>\n> {reply['text']}\n")
            lines.append("\n</details>\n")
    
    path.write_text(existing + "\n".join(lines))
