"""Google Sheets sink.

Header handling is the fiddly part: the sheet is the user's, they may have
reordered or renamed columns, and a run must not shuffle them. So the existing
header row is authoritative — new fields are appended to the right, and rows
are mapped onto whatever order the sheet already has.

Dedupe reads one column rather than the whole sheet, which keeps a re-run
against a few thousand rows to a single cheap request.
"""

from __future__ import annotations

import json
from typing import Any

from ..core import Document, register
from ..google_auth import service
from ..templating import render


def _cell(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    if value is None:
        return ""
    if isinstance(value, (int, float, bool, str)):
        return value
    return str(value)


def _col_letter(idx: int) -> str:
    """0-based index to A1 column label (0 -> A, 26 -> AA)."""
    label = ""
    idx += 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        label = chr(65 + rem) + label
    return label


@register("sink", "gsheet")
class GSheetSink:
    def __init__(
        self,
        spreadsheet_id: str,
        tab: str = "Sheet1",
        dedupe_key: str | None = None,
        create_tab: bool = True,
    ) -> None:
        self.spreadsheet_id = spreadsheet_id
        self.tab_template = tab
        self.dedupe_key = dedupe_key
        self.create_tab = create_tab
        self._headers: dict[str, list[str]] = {}
        self._seen: dict[str, set[str]] = {}
        self._tabs: set[str] | None = None

    @property
    def api(self) -> Any:
        return service("sheets", "v4")

    def _existing_tabs(self) -> set[str]:
        if self._tabs is None:
            meta = (
                self.api.spreadsheets()
                .get(spreadsheetId=self.spreadsheet_id, fields="sheets.properties.title")
                .execute()
            )
            self._tabs = {s["properties"]["title"] for s in meta.get("sheets", [])}
        return self._tabs

    def _ensure_tab(self, tab: str) -> None:
        if tab in self._existing_tabs():
            return
        if not self.create_tab:
            raise ValueError(f"tab {tab!r} does not exist and create_tab is false")
        self.api.spreadsheets().batchUpdate(
            spreadsheetId=self.spreadsheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": tab}}}]},
        ).execute()
        assert self._tabs is not None
        self._tabs.add(tab)

    def _header(self, tab: str) -> list[str]:
        if tab in self._headers:
            return self._headers[tab]
        resp = (
            self.api.spreadsheets()
            .values()
            .get(spreadsheetId=self.spreadsheet_id, range=f"'{tab}'!1:1")
            .execute()
        )
        header = (resp.get("values") or [[]])[0]
        self._headers[tab] = list(header)
        return self._headers[tab]

    def _seen_keys(self, tab: str, header: list[str]) -> set[str]:
        if tab in self._seen:
            return self._seen[tab]
        keys: set[str] = set()
        if self.dedupe_key and self.dedupe_key in header:
            col = _col_letter(header.index(self.dedupe_key))
            resp = (
                self.api.spreadsheets()
                .values()
                .get(spreadsheetId=self.spreadsheet_id, range=f"'{tab}'!{col}2:{col}")
                .execute()
            )
            keys = {
                str(r[0]) for r in (resp.get("values") or []) if r and str(r[0]).strip()
            }
        self._seen[tab] = keys
        return keys

    def _write_header(self, tab: str, header: list[str]) -> None:
        self.api.spreadsheets().values().update(
            spreadsheetId=self.spreadsheet_id,
            range=f"'{tab}'!A1",
            valueInputOption="RAW",
            body={"values": [header]},
        ).execute()
        self._headers[tab] = header

    def write(self, docs: list[Document]) -> int:
        # One batch can span tabs when the tab name is templated by date.
        grouped: dict[str, list[dict[str, Any]]] = {}
        for doc in docs:
            if not doc.records:
                continue
            tab = render(self.tab_template, doc)
            grouped.setdefault(tab, []).extend(doc.records)

        total = 0
        for tab, records in grouped.items():
            self._ensure_tab(tab)
            header = list(self._header(tab))
            seen = self._seen_keys(tab, header)

            fresh = []
            for rec in records:
                if self.dedupe_key:
                    key = str(rec.get(self.dedupe_key) or "")
                    if key and key in seen:
                        continue
                    if key:
                        seen.add(key)
                fresh.append(rec)
            if not fresh:
                continue

            added = [k for rec in fresh for k in rec if k not in header]
            if added or not header:
                # dict.fromkeys dedupes while preserving first-seen order.
                header.extend(dict.fromkeys(added))
                self._write_header(tab, header)

            rows = [[_cell(rec.get(col)) for col in header] for rec in fresh]
            self.api.spreadsheets().values().append(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{tab}'!A1",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": rows},
            ).execute()
            total += len(rows)
        return total

    def close(self) -> None:
        pass
