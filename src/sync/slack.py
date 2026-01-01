"""Slack sync - works with user tokens (xoxp-) or bot tokens (xoxb-).

User tokens are recommended as they access all channels you're in without adding a bot.
See src/sync/config.py for required scopes.
"""
import json
from pathlib import Path
from datetime import datetime

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from src.sync.config import INTERNAL_DOMAIN

USER_CACHE_FILE = "slack_users.json"


def sync_slack(output_dir: Path, token: str, state: dict) -> dict:
    """Sync Slack channels to markdown files. Returns updated state."""
    output_dir.mkdir(parents=True, exist_ok=True)
    client = WebClient(token=token)

    # Load/update user cache
    users = _load_user_cache(output_dir)
    new_state = {}

    channels = _get_channels(client)
    for channel in channels:
        channel_id = channel["id"]
        channel_name = channel["name"]

        last_ts = state.get(channel_id, {}).get("last_ts", "0")
        messages, latest_ts = _get_messages_with_threads(client, channel_id, users, oldest=last_ts)

        if not messages:
            new_state[channel_id] = state.get(channel_id, {"last_ts": "0", "name": channel_name})
            continue

        md_path = output_dir / f"{channel_name}.md"
        _append_messages_to_md(md_path, channel_name, messages)
        new_state[channel_id] = {"last_ts": latest_ts, "name": channel_name}

    _save_user_cache(output_dir, users)
    return new_state


def _load_user_cache(output_dir: Path) -> dict:
    cache_path = output_dir / USER_CACHE_FILE
    if cache_path.exists():
        return json.loads(cache_path.read_text())
    return {}


def _save_user_cache(output_dir: Path, users: dict):
    cache_path = output_dir / USER_CACHE_FILE
    cache_path.write_text(json.dumps(users, indent=2))


def _get_channels(client: WebClient) -> list:
    """Get list of channels the bot has access to."""
    try:
        result = client.conversations_list(types="public_channel,private_channel", limit=200)
        return result.get("channels", [])
    except SlackApiError as e:
        print(f"Slack API error listing channels: {e}")
        return []


def _get_user_info(client: WebClient, user_id: str, users: dict) -> dict:
    """Get user info with caching. Returns {name, email, is_internal}."""
    if user_id in users:
        return users[user_id]

    try:
        result = client.users_info(user=user_id)
        user = result.get("user", {})
        profile = user.get("profile", {})
        email = profile.get("email", "")

        info = {
            "name": profile.get("real_name") or user.get("name") or user_id,
            "email": email,
            "is_internal": email.endswith(f"@{INTERNAL_DOMAIN}") if email else False,
        }
        users[user_id] = info
        return info
    except SlackApiError:
        return {"name": user_id, "email": "", "is_internal": False}


def _get_messages_with_threads(client: WebClient, channel_id: str, users: dict, oldest: str = "0") -> tuple[list, str]:
    """Get messages and their thread replies. Returns (messages_with_threads, latest_ts)."""
    try:
        result = client.conversations_history(channel=channel_id, oldest=oldest, limit=200)
        messages = result.get("messages", [])
        if not messages:
            return [], oldest

        enriched = []
        for msg in messages:
            user_info = _get_user_info(client, msg.get("user", ""), users)
            enriched_msg = {
                "ts": msg["ts"],
                "text": msg.get("text", ""),
                "user": user_info,
                "replies": [],
            }

            # Fetch thread replies if this message has them
            if msg.get("thread_ts") == msg["ts"] and msg.get("reply_count", 0) > 0:
                enriched_msg["replies"] = _get_thread_replies(client, channel_id, msg["ts"], users, oldest)

            enriched.append(enriched_msg)

        latest_ts = max(m["ts"] for m in messages)
        return enriched, latest_ts
    except SlackApiError as e:
        print(f"Slack API error fetching messages: {e}")
        return [], oldest


def _get_thread_replies(client: WebClient, channel_id: str, thread_ts: str, users: dict, oldest: str) -> list:
    """Get replies in a thread, excluding the parent message."""
    try:
        result = client.conversations_replies(channel=channel_id, ts=thread_ts, oldest=oldest, limit=100)
        replies = result.get("messages", [])[1:]  # Skip parent message

        enriched = []
        for reply in replies:
            if float(reply["ts"]) <= float(oldest):
                continue  # Skip already-synced replies
            user_info = _get_user_info(client, reply.get("user", ""), users)
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


def _append_messages_to_md(path: Path, channel_name: str, messages: list):
    """Append messages to a markdown file with thread structure."""
    existing = path.read_text() if path.exists() else f"# #{channel_name}\n\n"

    lines = []
    for msg in sorted(messages, key=lambda m: float(m["ts"])):
        user_str = _format_user(msg["user"])
        time_str = _format_timestamp(msg["ts"])

        lines.append(f"---\n### {user_str}\n*{time_str}*\n\n{msg['text']}\n")

        # Format thread replies indented
        if msg["replies"]:
            lines.append("\n<details><summary>📎 Thread replies</summary>\n")
            for reply in msg["replies"]:
                reply_user = _format_user(reply["user"])
                reply_time = _format_timestamp(reply["ts"])
                lines.append(f"\n> {reply_user} *{reply_time}*\n>\n> {reply['text']}\n")
            lines.append("\n</details>\n")

    path.write_text(existing + "\n".join(lines))
