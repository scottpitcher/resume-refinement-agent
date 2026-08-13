"""
Thin wrapper around the Drive API for the folder/file operations the
pipeline needs: finding folders, listing contents, creating folders,
and copying files (a native Google Doc copy stays a native Google Doc,
formatting intact -- no download/rebuild involved).
"""

from typing import List, Optional

from .auth import get_drive_service

FOLDER_MIME = "application/vnd.google-apps.folder"
DOC_MIME = "application/vnd.google-apps.document"


class DriveClient:
    def __init__(self):
        self.service = get_drive_service()

    def find_folder(self, name: str, parent_id: Optional[str] = None) -> Optional[dict]:
        """Finds a folder by exact name, optionally scoped to a parent folder."""
        query = f"name = '{_escape(name)}' and mimeType = '{FOLDER_MIME}' and trashed = false"
        if parent_id:
            query += f" and '{parent_id}' in parents"
        results = self.service.files().list(
            q=query, fields="files(id, name)", spaces="drive"
        ).execute()
        files = results.get("files", [])
        return files[0] if files else None

    def create_folder(self, name: str, parent_id: Optional[str] = None) -> dict:
        metadata = {"name": name, "mimeType": FOLDER_MIME}
        if parent_id:
            metadata["parents"] = [parent_id]
        return self.service.files().create(body=metadata, fields="id, name").execute()

    def find_or_create_folder(self, name: str, parent_id: Optional[str] = None) -> dict:
        existing = self.find_folder(name, parent_id)
        if existing:
            return existing
        return self.create_folder(name, parent_id)

    def list_children(self, parent_id: str) -> List[dict]:
        """Lists all non-trashed files/folders directly inside a folder."""
        results = self.service.files().list(
            q=f"'{parent_id}' in parents and trashed = false",
            fields="files(id, name, mimeType)",
            spaces="drive",
            pageSize=200,
        ).execute()
        return results.get("files", [])

    def copy_file(self, file_id: str, new_name: str, parent_id: str) -> dict:
        """
        Server-side copy. For a native Google Doc, this duplicates the doc
        internally -- formatting is preserved exactly because the bytes are
        never downloaded or reconstructed.
        """
        body = {"name": new_name, "parents": [parent_id]}
        return self.service.files().copy(
            fileId=file_id, body=body, fields="id, name, mimeType"
        ).execute()

    def get_file_metadata(self, file_id: str) -> dict:
        return self.service.files().get(
            fileId=file_id, fields="id, name, mimeType, parents"
        ).execute()


def _escape(value: str) -> str:
    return value.replace("'", "\\'")
