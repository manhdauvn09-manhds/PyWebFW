"""CSV export helper shared by every admin "Export CSV" endpoint.

Cells beginning with =, +, - or @ are prefixed with an apostrophe so opening
the file in Excel/Sheets cannot execute formula injection.
"""
from __future__ import annotations

import csv
import io
from typing import Any, Mapping, Sequence
from urllib.parse import quote

from fastapi import Response

_FORMULA_PREFIXES = ("=", "+", "-", "@")


class CsvExporter:
    @staticmethod
    def _sanitize(value: Any) -> str:
        text = "" if value is None else str(value)
        if text.startswith(_FORMULA_PREFIXES):
            return f"'{text}"
        return text

    @classmethod
    def to_csv(cls, rows: Sequence[Mapping[str, Any]], columns: Sequence[str]) -> str:
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(columns)
        for row in rows:
            writer.writerow([cls._sanitize(row.get(col)) for col in columns])
        return buffer.getvalue()

    @classmethod
    def response(cls, filename: str, rows: Sequence[Mapping[str, Any]],
                 columns: Sequence[str]) -> Response:
        return Response(
            content=cls.to_csv(rows, columns),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
        )
