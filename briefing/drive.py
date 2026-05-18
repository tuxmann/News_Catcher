"""Google Drive upload and retention cleanup."""

from __future__ import annotations

import logging
import mimetypes
from datetime import datetime, timedelta, timezone
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def _drive_service(credentials_path: str):
    creds = service_account.Credentials.from_service_account_file(
        credentials_path,
        scopes=SCOPES,
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def upload_file(
    local_path: Path,
    folder_id: str,
    *,
    credentials_path: str,
    remote_name: str | None = None,
) -> str:
    """Upload a file to Drive folder; return file ID."""
    local_path = Path(local_path)
    name = remote_name or local_path.name
    mime, _ = mimetypes.guess_type(name)
    mime = mime or "application/octet-stream"
    service = _drive_service(credentials_path)
    metadata = {"name": name, "parents": [folder_id]}
    media = MediaFileUpload(str(local_path), mimetype=mime, resumable=True)
    created = (
        service.files()
        .create(body=metadata, media_body=media, fields="id")
        .execute()
    )
    file_id = created["id"]
    logger.info("Uploaded %s → Drive file %s", name, file_id)
    return file_id


def delete_files_older_than(
    folder_id: str,
    *,
    credentials_path: str,
    retention_days: int,
) -> int:
    """Delete files in folder older than retention_days. Returns count deleted."""
    if retention_days <= 0:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    service = _drive_service(credentials_path)
    deleted = 0
    page_token = None
    while True:
        resp = (
            service.files()
            .list(
                q=f"'{folder_id}' in parents and trashed=false",
                fields="nextPageToken, files(id, name, createdTime)",
                pageToken=page_token,
            )
            .execute()
        )
        for f in resp.get("files", []):
            created_raw = f.get("createdTime")
            if not created_raw:
                continue
            created = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
            if created < cutoff:
                service.files().delete(fileId=f["id"]).execute()
                logger.info("Deleted old Drive file: %s", f.get("name"))
                deleted += 1
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return deleted
