"""Google Drive connector - syncs Docs and Sheets to markdown."""

import json
import asyncio
import base64
import os
from pathlib import Path
from datetime import datetime
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


SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]

GDRIVE_RATE_LIMIT = 5
GDRIVE_CONCURRENT_EXPORTS = 5

# Excluded folder paths (case-insensitive, comma-separated)
# Can be folder names ("Archive"), paths ("Projects/Archive"), or folder IDs
# Matches recursively - excludes all docs under matching folders
_excluded_folders_raw = os.getenv("GDRIVE_EXCLUDED_FOLDERS", "")
_excluded_folders_parsed = [f.strip() for f in _excluded_folders_raw.split(",") if f.strip()]

# Separate folder IDs from path patterns (IDs are long alphanumeric, no slashes/spaces)
def _is_folder_id(s: str) -> bool:
    return len(s) > 20 and "/" not in s and " " not in s and s.replace("-", "").replace("_", "").isalnum()

GDRIVE_EXCLUDED_FOLDER_IDS = {f for f in _excluded_folders_parsed if _is_folder_id(f)}
GDRIVE_EXCLUDED_FOLDERS = [f.lower() for f in _excluded_folders_parsed if not _is_folder_id(f)]


class GDriveConnector(Connector):
    """Syncs Google Drive docs and sheets to markdown files."""
    
    name = "gdrive"
    env_key = "GDRIVE_CREDS"
    
    def __init__(self):
        super().__init__()
        self._creds: Credentials | None = None
        self._creds_path: str = ""
    
    @property
    def enabled(self) -> bool:
        # Can be enabled via file path or base64-encoded creds
        return bool(os.getenv("GDRIVE_CREDS") or os.getenv("GDRIVE_CREDS_BASE64"))
    
    def setup(self) -> bool:
        """Load credentials and test GDrive connection."""
        self._creds = _load_credentials()
        if not self._creds:
            print(f"  ✗ GDrive: No valid credentials found")
            return False
        
        # Test the connection
        try:
            service = build("drive", "v3", credentials=self._creds)
            about = service.about().get(fields="user").execute()
            email = about.get("user", {}).get("emailAddress", "unknown")
            print(f"  ✓ GDrive: Connected as {email}")
            return True
        except HttpError as e:
            print(f"  ✗ GDrive: Connection failed - {e}")
            return False
    
    async def download(
        self,
        output_dir: Path,
        state: dict,
        state_manager: "StateManager | None" = None,
    ) -> tuple[dict, ConnectorResult]:
        """Sync Google Drive docs to markdown files."""
        output_dir.mkdir(parents=True, exist_ok=True)
        
        if not self._creds:
            self._creds = _load_credentials()
            if not self._creds:
                return state, ConnectorResult(success=False, message="No credentials")
        
        rate_limiter = RateLimiter(GDRIVE_RATE_LIMIT)
        drive_service = build("drive", "v3", credentials=self._creds)
        
        # Build folder cache if we have exclusions
        folder_cache: dict[str, dict] = {}
        has_exclusions = GDRIVE_EXCLUDED_FOLDERS or GDRIVE_EXCLUDED_FOLDER_IDS
        if has_exclusions:
            exclusion_strs = list(GDRIVE_EXCLUDED_FOLDERS) + [f"id:{fid[:12]}..." for fid in GDRIVE_EXCLUDED_FOLDER_IDS]
            print(f"  🚫 GDrive: Excluding folders: {', '.join(exclusion_strs)}")
            folder_cache = await _build_folder_cache(drive_service, rate_limiter)
        
        docs = await _list_all_docs(drive_service, rate_limiter)
        print(f"  📄 GDrive: Found {len(docs)} documents")
        
        # Filter out docs in excluded folders and handle retroactive deletion
        excluded_count = 0
        if has_exclusions:
            filtered_docs = []
            for doc in docs:
                if _is_in_excluded_folder(doc, folder_cache):
                    excluded_count += 1
                    # Retroactive deletion: remove .md file if it exists
                    _delete_doc_md_file(doc, state, output_dir)
                    continue
                filtered_docs.append(doc)
            docs = filtered_docs
            if excluded_count > 0:
                print(f"     Excluded {excluded_count} docs in excluded folders")
        
        docs_to_sync = []
        new_state = {}
        
        for doc in docs:
            doc_id = doc["id"]
            modified_time = doc.get("modifiedTime", "")
            last_modified = state.get(doc_id, {}).get("modified_time")
            
            if last_modified == modified_time:
                new_state[doc_id] = state[doc_id]
                continue
            
            docs_to_sync.append(doc)
        
        skipped_count = len(docs) - len(docs_to_sync)
        
        semaphore = asyncio.Semaphore(GDRIVE_CONCURRENT_EXPORTS)
        
        async def process_doc(doc: dict) -> tuple[str, dict | None]:
            async with semaphore:
                doc_id, doc_state = await _export_and_save_doc(
                    doc, self._creds, output_dir, state, rate_limiter
                )
                
                if doc_state and state_manager:
                    await state_manager.update_item(self.name, doc_id, doc_state)
                
                return doc_id, doc_state
        
        results = await asyncio.gather(*[process_doc(doc) for doc in docs_to_sync])
        
        synced_count = 0
        for doc_id, doc_state in results:
            if doc_state:
                new_state[doc_id] = doc_state
                synced_count += 1
        
        print(f"  ✓ GDrive: {synced_count} docs synced, {skipped_count} unchanged")
        return new_state, ConnectorResult(
            success=True,
            items_synced=synced_count,
            items_skipped=skipped_count,
        )


# --- Rate limiter and helpers ---

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


async def _export_and_save_doc(
    doc: dict,
    creds: Credentials,
    output_dir: Path,
    state: dict,
    rate_limiter: RateLimiter,
) -> tuple[str, dict | None]:
    """Export and save a single document."""
    doc_id = doc["id"]
    doc_name = doc["name"]
    modified_time = doc.get("modifiedTime", "")
    
    await rate_limiter.acquire()
    
    is_sheet = "spreadsheet" in doc["mimeType"]
    
    if is_sheet:
        content = await _run_in_executor(_export_spreadsheet_sync, creds, doc_id)
    else:
        content = await _run_in_executor(_export_doc_sync, creds, doc_id)
    
    if not content:
        return doc_id, None
    
    md_content = _format_doc_markdown(doc, content)
    safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in doc_name)
    md_path = output_dir / f"{safe_name}.md"
    md_path.write_text(md_content)
    
    doc_type = "sheet" if is_sheet else "doc"
    status = "new" if doc_id not in state else "updated"
    print(f"     [{status}] {doc_name} ({doc_type})")
    
    return doc_id, {"name": doc_name, "modified_time": modified_time}


def _export_spreadsheet_sync(creds: Credentials, spreadsheet_id: str) -> str | None:
    """Export a Google Sheet as markdown tables with formulas."""
    try:
        sheets_service = build("sheets", "v4", credentials=creds)
        
        spreadsheet = sheets_service.spreadsheets().get(
            spreadsheetId=spreadsheet_id,
            includeGridData=False,
        ).execute()
        
        sheets = spreadsheet.get("sheets", [])
        if not sheets:
            return None
        
        ranges = [sheet["properties"]["title"] for sheet in sheets]
        
        values_response = sheets_service.spreadsheets().values().batchGet(
            spreadsheetId=spreadsheet_id,
            ranges=ranges,
            valueRenderOption="FORMATTED_VALUE",
        ).execute()
        
        formulas_response = sheets_service.spreadsheets().values().batchGet(
            spreadsheetId=spreadsheet_id,
            ranges=ranges,
            valueRenderOption="FORMULA",
        ).execute()
        
        values_ranges = values_response.get("valueRanges", [])
        formulas_ranges = formulas_response.get("valueRanges", [])
        
        md_parts = []
        for i, sheet in enumerate(sheets):
            sheet_name = sheet["properties"]["title"]
            values = values_ranges[i].get("values", []) if i < len(values_ranges) else []
            formulas = formulas_ranges[i].get("values", []) if i < len(formulas_ranges) else []
            md_parts.append(_format_sheet_as_markdown(sheet_name, values, formulas))
        
        return "\n\n".join(md_parts)
    
    except HttpError as e:
        print(f"  ✗ Failed to export spreadsheet: {e}")
        return None


def _export_doc_sync(creds: Credentials, doc_id: str) -> str | None:
    """Export a Google Doc to plain text."""
    try:
        drive_service = build("drive", "v3", credentials=creds)
        
        content = drive_service.files().export(
            fileId=doc_id,
            mimeType="text/plain",
        ).execute()
        return content.decode("utf-8") if isinstance(content, bytes) else content
    except HttpError as e:
        print(f"  ✗ Failed to export doc: {e}")
        return None


async def _list_all_docs(service, rate_limiter: RateLimiter) -> list:
    """List Google Docs and Sheets from My Drive and all Shared Drives."""
    all_docs = []
    
    await rate_limiter.acquire()
    my_drive_docs = await _run_in_executor(_list_docs_in_drive_sync, service, None)
    if my_drive_docs:
        print(f"     My Drive: {len(my_drive_docs)} docs")
    all_docs.extend(my_drive_docs)
    
    try:
        await rate_limiter.acquire()
        drives = await _run_in_executor(
            lambda: service.drives().list(pageSize=50).execute()
        )
        
        for drive in drives.get("drives", []):
            await rate_limiter.acquire()
            drive_docs = await _run_in_executor(_list_docs_in_drive_sync, service, drive["id"])
            print(f"     {drive['name']}: {len(drive_docs)} docs")
            all_docs.extend(drive_docs)
    except HttpError as e:
        print(f"  ✗ Error listing shared drives: {e}")
    
    return all_docs


def _list_docs_in_drive_sync(service, drive_id: str | None) -> list:
    """List Google Docs and Sheets in a specific drive (with pagination)."""
    query = "(mimeType='application/vnd.google-apps.document' or mimeType='application/vnd.google-apps.spreadsheet')"
    fields = "nextPageToken, files(id, name, mimeType, modifiedTime, owners, parents)"
    all_docs = []
    page_token = None
    
    try:
        while True:
            if drive_id:
                params = {
                    "q": query,
                    "driveId": drive_id,
                    "corpora": "drive",
                    "supportsAllDrives": True,
                    "includeItemsFromAllDrives": True,
                    "fields": fields,
                    "pageSize": 100,
                }
            else:
                params = {
                    "q": query,
                    "supportsAllDrives": True,
                    "includeItemsFromAllDrives": True,
                    "fields": fields,
                    "pageSize": 100,
                }
            
            if page_token:
                params["pageToken"] = page_token
            
            results = service.files().list(**params).execute()
            all_docs.extend(results.get("files", []))
            
            page_token = results.get("nextPageToken")
            if not page_token:
                break
        
        return all_docs
    except HttpError as e:
        print(f"  ✗ GDrive API error listing files: {e}")
        return all_docs


async def _build_folder_cache(service, rate_limiter: RateLimiter) -> dict[str, dict]:
    """Build a cache mapping folder IDs to {name, parents} for path resolution."""
    folder_cache = {}
    
    async def fetch_all_folders(query_params: dict) -> list:
        """Fetch all folders with pagination."""
        all_folders = []
        page_token = None
        
        while True:
            await rate_limiter.acquire()
            params = {**query_params, "pageSize": 500}
            if page_token:
                params["pageToken"] = page_token
            
            results = await _run_in_executor(
                lambda p=params: service.files().list(**p).execute()
            )
            all_folders.extend(results.get("files", []))
            
            page_token = results.get("nextPageToken")
            if not page_token:
                break
        
        return all_folders
    
    try:
        # Get all folders in My Drive (with pagination)
        my_drive_folders = await fetch_all_folders({
            "q": "mimeType='application/vnd.google-apps.folder'",
            "supportsAllDrives": True,
            "includeItemsFromAllDrives": True,
            "fields": "nextPageToken, files(id, name, parents)",
        })
        
        for folder in my_drive_folders:
            folder_cache[folder["id"]] = {
                "name": folder["name"],
                "parents": folder.get("parents", []),
            }
        
        # Also get folders from shared drives
        await rate_limiter.acquire()
        drives = await _run_in_executor(
            lambda: service.drives().list(pageSize=50).execute()
        )
        
        for drive in drives.get("drives", []):
            drive_folders = await fetch_all_folders({
                "q": "mimeType='application/vnd.google-apps.folder'",
                "driveId": drive["id"],
                "corpora": "drive",
                "supportsAllDrives": True,
                "includeItemsFromAllDrives": True,
                "fields": "nextPageToken, files(id, name, parents)",
            })
            for folder in drive_folders:
                folder_cache[folder["id"]] = {
                    "name": folder["name"],
                    "parents": folder.get("parents", []),
                }
        
        print(f"     Folder cache: {len(folder_cache)} folders indexed")
    except HttpError as e:
        print(f"  ⚠ GDrive: Error building folder cache: {e}")
    
    return folder_cache


def _resolve_folder_path(folder_id: str, folder_cache: dict[str, dict], seen: set | None = None) -> str:
    """Resolve full folder path from folder ID (e.g., 'Projects/Archive/2024')."""
    if seen is None:
        seen = set()
    
    if folder_id in seen:
        return ""  # Prevent infinite loops
    seen.add(folder_id)
    
    folder_info = folder_cache.get(folder_id)
    if not folder_info:
        return ""
    
    folder_name = folder_info["name"]
    parents = folder_info.get("parents", [])
    
    if not parents:
        return folder_name
    
    # Recursively resolve parent path
    parent_path = _resolve_folder_path(parents[0], folder_cache, seen)
    if parent_path:
        return f"{parent_path}/{folder_name}"
    return folder_name


def _get_folder_ancestry(folder_id: str, folder_cache: dict[str, dict], seen: set | None = None) -> list[str]:
    """Get list of all folder IDs in the ancestry chain (including self)."""
    if seen is None:
        seen = set()
    
    if folder_id in seen or folder_id not in folder_cache:
        return []
    seen.add(folder_id)
    
    result = [folder_id]
    parents = folder_cache[folder_id].get("parents", [])
    if parents:
        result.extend(_get_folder_ancestry(parents[0], folder_cache, seen))
    return result


def _is_in_excluded_folder(doc: dict, folder_cache: dict[str, dict]) -> bool:
    """Check if a document is in an excluded folder (matches path or ID recursively)."""
    if not GDRIVE_EXCLUDED_FOLDERS and not GDRIVE_EXCLUDED_FOLDER_IDS:
        return False
    
    parents = doc.get("parents", [])
    if not parents:
        return False
    
    parent_id = parents[0]
    
    # Check folder IDs first (faster, exact match)
    if GDRIVE_EXCLUDED_FOLDER_IDS:
        ancestry = _get_folder_ancestry(parent_id, folder_cache)
        for folder_id in ancestry:
            if folder_id in GDRIVE_EXCLUDED_FOLDER_IDS:
                return True
    
    # Check path patterns
    if not GDRIVE_EXCLUDED_FOLDERS:
        return False
    
    folder_path = _resolve_folder_path(parent_id, folder_cache)
    if not folder_path:
        return False
    
    folder_path_lower = folder_path.lower()
    
    # Check if any excluded folder matches as a path segment
    for excluded in GDRIVE_EXCLUDED_FOLDERS:
        # Match as full path segment: "Archive" matches "Projects/Archive" or "Archive/subfolder"
        # but not "MyArchive" or "Archives"
        if excluded == folder_path_lower:
            return True
        if folder_path_lower.startswith(excluded + "/"):
            return True
        if ("/" + excluded) in folder_path_lower:
            # Check it's a full segment match, not partial
            idx = folder_path_lower.find("/" + excluded)
            rest = folder_path_lower[idx + len(excluded) + 1:]
            if rest == "" or rest.startswith("/"):
                return True
    
    return False


def _delete_doc_md_file(doc: dict, state: dict, output_dir: Path):
    """Delete the .md file for a document if it exists (retroactive exclusion)."""
    doc_id = doc["id"]
    doc_name = doc["name"]
    
    # Try to find and delete the file
    # First check state for the stored name
    stored_name = state.get(doc_id, {}).get("name")
    names_to_try = [stored_name, doc_name] if stored_name else [doc_name]
    
    for name in names_to_try:
        if not name:
            continue
        safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in name)
        md_path = output_dir / f"{safe_name}.md"
        if md_path.exists():
            md_path.unlink()
            print(f"     [deleted] {name} (now in excluded folder)")
            return


def _format_sheet_as_markdown(sheet_name: str, values: list, formulas: list) -> str:
    """Format a single sheet as a markdown table with formulas shown."""
    if not values:
        return f"## 📊 {sheet_name}\n\n*Empty sheet*"
    
    lines = [f"## 📊 {sheet_name}", ""]
    
    max_cols = max(len(row) for row in values) if values else 0
    if max_cols == 0:
        return f"## 📊 {sheet_name}\n\n*Empty sheet*"
    
    def pad_row(row, length):
        return list(row) + [""] * (length - len(row))
    
    for row_idx, value_row in enumerate(values):
        value_row = pad_row(value_row, max_cols)
        formula_row = pad_row(formulas[row_idx], max_cols) if row_idx < len(formulas) else [""] * max_cols
        
        cells = []
        for col_idx, val in enumerate(value_row):
            formula = formula_row[col_idx] if col_idx < len(formula_row) else ""
            cell_str = str(val).replace("|", "\\|").replace("\n", " ")
            
            if formula and str(formula).startswith("="):
                formula_str = str(formula).replace("|", "\\|")
                cell_str = f"{cell_str} `{formula_str}`"
            
            cells.append(cell_str)
        
        lines.append("| " + " | ".join(cells) + " |")
        
        if row_idx == 0:
            lines.append("| " + " | ".join(["---"] * max_cols) + " |")
    
    return "\n".join(lines)


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


def _load_credentials() -> Credentials | None:
    """Load credentials from file path or GDRIVE_CREDS_BASE64 env var."""
    # Try base64-encoded JSON from environment first (for deployed environments)
    creds_base64 = os.getenv("GDRIVE_CREDS_BASE64")
    if creds_base64:
        try:
            creds_json = base64.b64decode(creds_base64).decode("utf-8")
            creds_data = json.loads(creds_json)
            if creds_data.get("type") == "service_account":
                return service_account.Credentials.from_service_account_info(creds_data, scopes=SCOPES)
            return Credentials.from_authorized_user_info(creds_data, SCOPES)
        except Exception as e:
            print(f"  ✗ GDrive: Failed to load credentials from GDRIVE_CREDS_BASE64: {e}")
            return None
    
    # Fall back to file path
    creds_path = os.getenv("GDRIVE_CREDS")
    if not creds_path:
        return None
    
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
