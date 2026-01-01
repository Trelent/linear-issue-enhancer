import json
from pathlib import Path
from datetime import datetime

from google.oauth2.credentials import Credentials
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from src.sync.config import INTERNAL_DOMAIN

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


def sync_gdrive(output_dir: Path, creds_path: str, state: dict) -> dict:
    """Sync Google Drive docs to markdown files. Returns updated state."""
    output_dir.mkdir(parents=True, exist_ok=True)

    creds = _load_credentials(creds_path)
    if not creds:
        return state

    service = build("drive", "v3", credentials=creds)
    new_state = {}

    docs = _list_docs(service)
    for doc in docs:
        doc_id = doc["id"]
        doc_name = doc["name"]
        modified_time = doc.get("modifiedTime", "")

        # Skip if not modified since last sync
        last_modified = state.get(doc_id, {}).get("modified_time")
        if last_modified == modified_time:
            new_state[doc_id] = state[doc_id]
            continue

        content = _export_doc(service, doc_id, doc["mimeType"])
        if not content:
            continue

        # Build markdown with metadata header
        md_content = _format_doc_markdown(doc, content)

        safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in doc_name)
        md_path = output_dir / f"{safe_name}.md"
        md_path.write_text(md_content)

        new_state[doc_id] = {"name": doc_name, "modified_time": modified_time}

    return new_state


def _format_doc_markdown(doc: dict, content: str) -> str:
    """Format document with metadata header."""
    owners = doc.get("owners", [])
    modified = doc.get("modifiedTime", "")

    lines = [f"# {doc['name']}", ""]

    # Metadata block
    lines.append("| Property | Value |")
    lines.append("|----------|-------|")

    if modified:
        dt = datetime.fromisoformat(modified.replace("Z", "+00:00"))
        lines.append(f"| Last Modified | {dt.strftime('%Y-%m-%d %H:%M')} |")

    for owner in owners:
        email = owner.get("emailAddress", "")
        name = owner.get("displayName", email)
        tag = "internal" if email.endswith(f"@{INTERNAL_DOMAIN}") else "external"
        lines.append(f"| Owner | {name} <{email}> [{tag}] |")

    lines.append(f"| Source | Google Drive |")
    lines.append(f"| Doc ID | `{doc['id']}` |")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(content)

    return "\n".join(lines)


def _load_credentials(creds_path: str) -> Credentials | None:
    """Load credentials from a service account JSON file."""
    path = Path(creds_path)
    if not path.exists():
        print(f"Credentials file not found: {creds_path}")
        return None

    try:
        creds_data = json.loads(path.read_text())

        if creds_data.get("type") == "service_account":
            return service_account.Credentials.from_service_account_file(creds_path, scopes=SCOPES)

        return Credentials.from_authorized_user_file(creds_path, SCOPES)
    except Exception as e:
        print(f"Failed to load credentials: {e}")
        return None


def _list_docs(service) -> list:
    """List Google Docs and Sheets with owner info."""
    try:
        results = service.files().list(
            q="(mimeType='application/vnd.google-apps.document' or mimeType='application/vnd.google-apps.spreadsheet')",
            fields="files(id, name, mimeType, modifiedTime, owners)",
            pageSize=100,
        ).execute()
        return results.get("files", [])
    except HttpError as e:
        print(f"Google Drive API error listing files: {e}")
        return []


def _export_doc(service, doc_id: str, mime_type: str) -> str | None:
    """Export a Google Doc or Sheet to text."""
    if "document" in mime_type:
        export_mime = "text/plain"
    elif "spreadsheet" in mime_type:
        export_mime = "text/csv"
    else:
        return None

    try:
        content = service.files().export(fileId=doc_id, mimeType=export_mime).execute()
        return content.decode("utf-8") if isinstance(content, bytes) else content
    except HttpError as e:
        print(f"Failed to export doc {doc_id}: {e}")
        return None
