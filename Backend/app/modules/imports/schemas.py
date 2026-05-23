"""Pydantic schemas for the imports module."""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ImportKind = Literal["students", "teachers", "schools"]


class ImportPreviewRow(BaseModel):
    rowIndex: int  # 1-based, matches Excel row numbering
    valid: bool
    errors: list[str] = []
    data: dict[str, Any] = {}


class ImportSummary(BaseModel):
    total: int
    valid: int
    invalid: int


class ImportPreviewResponse(BaseModel):
    """POST /api/imports/{kind}/preview output."""

    kind: ImportKind
    headers: list[str]
    summary: ImportSummary
    rows: list[ImportPreviewRow]


class ImportCommitRequest(BaseModel):
    """POST /api/imports/{kind}/commit body — caller sends the validated rows
    back so the server doesn't have to re-parse the upload.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    rows: list[ImportPreviewRow] = Field(min_length=1, max_length=10000)
    skipInvalid: bool = True


class ImportCommitResponse(BaseModel):
    queued: int
    skipped: int
    taskId: str | None = None
