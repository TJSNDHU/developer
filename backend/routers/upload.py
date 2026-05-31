"""
routers/upload.py — Convert uploaded files to clean Markdown via Microsoft's
MarkItDown so the LLM can read PDFs, DOCX, XLSX, PPTX, images, audio, HTML,
CSV, etc. without burning tokens on raw binary noise.

POST /api/aurem-dev/upload/convert
  multipart-form-data file=<binary>
  → {ok, filename, content_type, original_size, markdown, md_size}

Supported by MarkItDown: PDF, PPTX, DOCX, XLSX, XLS, HTML, CSV, JSON, XML,
ZIP, EPUB, images (with OCR if model configured), audio (transcription
optional), Outlook .msg, plain text.
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Header, HTTPException, UploadFile

from cto_services.auth import current_dev

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/upload", tags=["Upload / MarkItDown"])

# Hard cap to protect the server from huge uploads. PDFs / decks rarely
# exceed this; if a user needs more they can split the file.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024   # 25 MB
# Markdown output cap so we don't blow the LLM context window.
MAX_MD_CHARS = 60_000


@router.post("/convert")
async def upload_convert(
    file: UploadFile = File(...),
    authorization: Optional[str] = Header(None),
) -> dict:
    """Read an uploaded file, convert it to Markdown via MarkItDown, and
    return the markdown body to the frontend so it can be appended to the
    chat prompt as clean LLM-readable text."""
    # Auth required so we don't expose this as an open conversion endpoint
    await current_dev(authorization)

    raw = await file.read()
    size = len(raw)
    if size == 0:
        raise HTTPException(400, "Empty upload")
    if size > MAX_UPLOAD_BYTES:
        raise HTTPException(
            413,
            f"File too large ({size // (1024 * 1024)}MB). "
            f"Max is {MAX_UPLOAD_BYTES // (1024 * 1024)}MB.",
        )

    # MarkItDown reads from disk via its converter pipeline, so we drop
    # the upload to a temp file with the original suffix (the suffix is
    # used internally for format detection).
    suffix = Path(file.filename or "").suffix or ""
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tf:
        tf.write(raw)
        tf.flush()

        try:
            # Import inside the handler so a missing optional dependency
            # in a stripped-down deploy doesn't break router import.
            from markitdown import MarkItDown
            md = MarkItDown()
            result = md.convert(tf.name)
        except ImportError:
            logger.exception("markitdown not installed")
            raise HTTPException(500, "MarkItDown library not installed on server")
        except Exception as e:
            logger.exception(f"markitdown convert failed for {file.filename!r}")
            raise HTTPException(415, f"Couldn't convert this file: {e}")

    text = (getattr(result, "text_content", None) or "").strip()
    if not text:
        raise HTTPException(415, "MarkItDown returned no readable content")

    truncated = False
    if len(text) > MAX_MD_CHARS:
        text = text[:MAX_MD_CHARS] + "\n\n... [truncated by server cap]"
        truncated = True

    return {
        "ok": True,
        "filename": file.filename or "upload",
        "content_type": file.content_type or "",
        "original_size": size,
        "md_size": len(text),
        "truncated": truncated,
        "markdown": text,
    }
