"""Google Drive store: resolve/create a nested folder path, then upload.

Folder resolution is cached per run — a month's worth of attachments otherwise
costs one extra API round trip each just to re-discover the same folder.
"""

from __future__ import annotations

import io
from typing import Any

from ..core import Document, register
from ..deps import missing
from ..google_auth import service
from ..templating import render

FOLDER_MIME = "application/vnd.google-apps.folder"


@register("store", "gdrive")
class GDriveStore:
    def __init__(
        self,
        path: str = "docufunnel/{{yyyymm}}",
        parent_id: str = "root",
        skip_existing: bool = True,
    ) -> None:
        self.path_template = path
        self.parent_id = parent_id
        # A re-run should not create a second copy of the same attachment.
        self.skip_existing = skip_existing
        self._folders: dict[str, str] = {}

    @property
    def api(self) -> Any:
        return service("drive", "v3")

    def _child_folder(self, name: str, parent: str) -> str:
        safe = name.replace("'", "\\'")
        q = (
            f"name = '{safe}' and mimeType = '{FOLDER_MIME}' "
            f"and '{parent}' in parents and trashed = false"
        )
        found = (
            self.api.files()
            .list(q=q, fields="files(id)", pageSize=1, spaces="drive")
            .execute()
            .get("files", [])
        )
        if found:
            return found[0]["id"]
        created = (
            self.api.files()
            .create(
                body={"name": name, "mimeType": FOLDER_MIME, "parents": [parent]},
                fields="id",
            )
            .execute()
        )
        return created["id"]

    def _folder_for(self, doc: Document) -> str:
        rendered = render(self.path_template, doc)
        if rendered in self._folders:
            return self._folders[rendered]
        parent = self.parent_id
        for segment in [s for s in rendered.split("/") if s]:
            parent = self._child_folder(segment, parent)
        self._folders[rendered] = parent
        return parent

    def _existing(self, name: str, folder: str) -> str | None:
        safe = name.replace("'", "\\'")
        q = f"name = '{safe}' and '{folder}' in parents and trashed = false"
        found = (
            self.api.files()
            .list(q=q, fields="files(id)", pageSize=1, spaces="drive")
            .execute()
            .get("files", [])
        )
        return found[0]["id"] if found else None

    def put(self, doc: Document) -> str:
        try:
            from googleapiclient.http import MediaIoBaseUpload
        except ImportError as exc:
            raise missing("google-api-python-client", "google", "the Drive store") from exc

        folder = self._folder_for(doc)
        if self.skip_existing:
            existing = self._existing(doc.filename, folder)
            if existing:
                return f"https://drive.google.com/file/d/{existing}"

        media = MediaIoBaseUpload(
            io.BytesIO(doc.data), mimetype=doc.mime, resumable=False
        )
        created = (
            self.api.files()
            .create(
                body={"name": doc.filename, "parents": [folder]},
                media_body=media,
                fields="id",
            )
            .execute()
        )
        return f"https://drive.google.com/file/d/{created['id']}"
