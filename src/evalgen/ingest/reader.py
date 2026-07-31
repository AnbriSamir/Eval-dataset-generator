"""Byte-level JSONL line reading shared by every loader (ADR-0001 rule 4).

Files are read as BYTES and decoded per line, not opened in text mode: with text mode,
one malformed UTF-8 sequence raises mid-iteration and discards an entire file of good
records (or, with ``errors="replace"``, silently corrupts content that then gets hashed
and embedded). Here a broken line becomes one typed ``invalid_encoding`` rejection and
every other line survives intact.

v1 materializes the whole file in memory — an explicitly accepted trade-off in the ADR
(streaming is a revisit-when-measured concern, not a contract change).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SourceLine(BaseModel):
    """One physical line: either decoded text or a decode error, never both/neither."""

    model_config = ConfigDict(frozen=True)

    line_no: int = Field(ge=1)
    text: str | None
    decode_error: str | None = None

    @model_validator(mode="after")
    def _exactly_one_outcome(self) -> SourceLine:
        if (self.text is None) == (self.decode_error is None):
            raise ValueError("SourceLine must carry exactly one of text / decode_error")
        return self


def read_source_lines(path: Path) -> list[SourceLine]:
    """Split raw bytes on ``\\n`` and decode each line independently (strict UTF-8).

    - A trailing newline does NOT produce a phantom empty last line (the empty chunk
      after the final ``\\n`` is an artifact of splitting, not a line — counting it
      would make ``lines_read`` disagree with what any editor shows).
    - A trailing ``\\r`` (CRLF files) is stripped per line, so the same corpus checked
      out with either line-ending convention yields byte-identical records.
    - Line numbers are 1-based: they are published in reports and provenance, and every
      tool a human will cross-check against (editors, ``sed -n``) counts from 1.
    """
    chunks = path.read_bytes().split(b"\n")
    if chunks and chunks[-1] == b"":
        chunks.pop()
    lines: list[SourceLine] = []
    for line_no, chunk in enumerate(chunks, start=1):
        payload = chunk.removesuffix(b"\r")
        try:
            lines.append(SourceLine(line_no=line_no, text=payload.decode("utf-8")))
        except UnicodeDecodeError as exc:
            # str(exc) names the codec/byte/offset but never embeds the raw bytes —
            # still scrubbed again by the report builder, belt and braces.
            lines.append(SourceLine(line_no=line_no, text=None, decode_error=str(exc)))
    return lines
