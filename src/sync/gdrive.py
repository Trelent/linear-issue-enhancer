import json
from pathlib import Path
from datetime import datetime

from google.oauth2.credentials import Credentials
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from src.sync.config import is_internal_email

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


def sync_gdrive(output_dir: Path, creds_path: str, state: dict) -> dict:
    """Sync Google Drive docs to markdown files. Returns updated state."""
    output_dir.mkdir(parents=True, exist_ok=True)

    creds = _load_credentials(creds_path)
    if not creds:
        return state

    service = build("drive", "v3", credentials=creds)
    new_state = {}

    docs = _list_all_docs(service)
    print(f"  📄 GDrive: Found {len(docs)} documents")

    synced_count = 0
    skipped_count = 0

    for doc in docs:
        doc_id = doc["id"]
        doc_name = doc["name"]
        modified_time = doc.get("modifiedTime", "")

        last_modified = state.get(doc_id, {}).get("modified_time")
        if last_modified == modified_time:
            new_state[doc_id] = state[doc_id]
            skipped_count += 1
            continue

        content = _export_doc(service, doc_id, doc["mimeType"])
        if not content:
            continue

        md_content = _format_doc_markdown(doc, content)
        safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in doc_name)
        md_path = output_dir / f"{safe_name}.md"
        md_path.write_text(md_content)

        new_state[doc_id] = {"name": doc_name, "modified_time": modified_time}
        synced_count += 1

        doc_type = "sheet" if "spreadsheet" in doc["mimeType"] else "doc"
        status = "new" if doc_id not in state else "updated"
        print(f"     [{status}] {doc_name} ({doc_type})")

    print(f"  ✓ GDrive: {synced_count} docs synced, {skipped_count} unchanged")
    return new_state


def _format_doc_markdown(doc: dict, content: str) -> str:
    """Format document with metadata header."""
    owners = doc.get("owners", [])
    modified = doc.get("modifiedTime", "")

    lines = [f"# {doc['name']}", ""]
    lines.append("| Property | Value |")
    lines.append("|----------|-------|")

    if modified:
        dt = datetime.fromisoformat(modified.replace("Z", "+00:00"))
        lines.append(f"| Last Modified | {dt.strftime('%Y-%m-%d %H:%M')} |")

    for owner in owners:
        email = owner.get("emailAddress", "")
        name = owner.get("displayName", email)
        tag = "internal" if is_internal_email(email) else "external"
        lines.append(f"| Owner | {name} <{email}> [{tag}] |")

    lines.append("| Source | Google Drive |")
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
        print(f"  ✗ GDrive: Credentials file not found: {creds_path}")
        return None

    try:
        creds_data = json.loads(path.read_text())
        if creds_data.get("type") == "service_account":
            return service_account.Credentials.from_service_account_file(creds_path, scopes=SCOPES)
        return Credentials.from_authorized_user_file(creds_path, SCOPES)
    except Exception as e:
        print(f"  ✗ GDrive: Failed to load credentials: {e}")
        return None


def _list_all_docs(service) -> list:
    """List Google Docs and Sheets from My Drive and all Shared Drives."""
    all_docs = []

    # Query My Drive
    my_drive_docs = _list_docs_in_drive(service, drive_id=None)
    if my_drive_docs:
        print(f"     My Drive: {len(my_drive_docs)} docs")
    all_docs.extend(my_drive_docs)

    # Query each Shared Drive
    try:
        drives = service.drives().list(pageSize=50).execute()
        for drive in drives.get("drives", []):
            drive_docs = _list_docs_in_drive(service, drive_id=drive["id"])
            print(f"     {drive['name']}: {len(drive_docs)} docs")
            all_docs.extend(drive_docs)
    except HttpError as e:
        print(f"  ✗ Error listing shared drives: {e}")

    return all_docs


def _list_docs_in_drive(service, drive_id: str | None) -> list:
    """List Google Docs and Sheets in a specific drive."""
    query = "(mimeType='application/vnd.google-apps.document' or mimeType='application/vnd.google-apps.spreadsheet')"

    try:
        if drive_id:
            results = service.files().list(
                q=query,
                driveId=drive_id,
                corpora="drive",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
                fields="files(id, name, mimeType, modifiedTime, owners)",
                pageSize=100,
            ).execute()
        else:
            results = service.files().list(
                q=query,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
                fields="files(id, name, mimeType, modifiedTime, owners)",
                pageSize=100,
            ).execute()
        return results.get("files", [])
    except HttpError as e:
        print(f"  ✗ GDrive API error listing files: {e}")
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
        content = service.files().export(
            fileId=doc_id,
            mimeType=export_mime,
        ).execute()
        return content.decode("utf-8") if isinstance(content, bytes) else content
    except HttpError as e:
        print(f"  ✗ Failed to export: {e}")
        return None
