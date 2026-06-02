"""
routers/upload.py — Convert uploaded files to LLM-readable form.

Two paths:

1. **Document path** (PDF / DOCX / XLSX / PPTX / HTML / CSV / text) →
   Microsoft's MarkItDown converts to clean Markdown so the chat LLM
   can read it without burning tokens on raw binary noise.

2. **Image path** (PNG / JPG / WEBP / GIF / BMP / screenshots) →
   MarkItDown returns nothing useful for images without OCR set up.
   Instead we route the image to an OpenRouter vision model (Gemini
   Flash 1.5) which both *describes* the image AND OCRs any visible
   text. The result comes back as Markdown the chat LLM can act on.

POST /api/aurem-dev/upload/convert
  multipart-form-data file=<binary>
  → {ok, filename, content_type, original_size, markdown, md_size,
     kind: "doc" | "image"}
"""
from __future__ import annotations

import base64
import logging
import mimetypes
import os
import tempfile
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, File, Header, HTTPException, UploadFile

from cto_services.auth import current_dev

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/upload", tags=["Upload / MarkItDown"])

# Hard cap to protect the server from huge uploads.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024   # 25 MB
# Markdown output cap so we don't blow the LLM context window.
MAX_MD_CHARS = 60_000

# What we consider an image — handled by the vision path, NOT MarkItDown.
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
IMAGE_MIMES = {"image/png", "image/jpeg", "image/jpg", "image/webp",
                "image/gif", "image/bmp"}

# Vision model — Gemini Flash 1.5 via OpenRouter is cheap, fast, and
# strong at both image description AND OCR. We pin a specific revision
# so a silent upstream rename can't surprise us.
_VISION_MODEL = os.getenv("AUREM_VISION_MODEL", "google/gemini-2.5-flash-lite")
_VISION_PROMPT = (
    "You are part of an AI coding assistant. The user just attached an "
    "image to their chat. Produce a structured Markdown response that "
    "the downstream model can act on. Sections:\n\n"
    "**Visual description** — what's in the image (UI screenshot, "
    "diagram, photo, chart, error stack). 2-3 sentences.\n\n"
    "**Extracted text** — verbatim text visible in the image, "
    "preserving layout / code formatting / error messages. If it's a "
    "code screenshot, fence the code with ```. If it's an error, "
    "include the full stack trace. If there's no text, write "
    "\"(no text in image)\".\n\n"
    "**Likely intent** — one line: what is the user probably asking the "
    "AI to do with this image?\n\n"
    "Be exhaustive on Extracted text — OCR every visible word. Do not "
    "summarise away error messages or stack traces; the user needs them "
    "verbatim to debug."
)


async def _describe_image_via_vision(raw: bytes, content_type: str,
                                     filename: str) -> str:
    """Send the image (as a base64 data URL) to a vision LLM and return
    its Markdown description. Returns an empty string on any failure so
    the caller can decide whether to surface a degraded response or
    bail with 415."""
    api_key = (
        os.getenv("OPENROUTER_API_KEY")
        or os.getenv("EMERGENT_LLM_KEY")
        or ""
    ).strip()
    if not api_key:
        logger.warning("vision OCR skipped — no OPENROUTER_API_KEY set")
        return ""

    mime = content_type or mimetypes.guess_type(filename)[0] or "image/png"
    b64 = base64.b64encode(raw).decode("ascii")
    data_url = f"data:{mime};base64,{b64}"

    payload = {
        "model": _VISION_MODEL,
        "max_tokens": 1200,
        "temperature": 0.2,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": _VISION_PROMPT},
                {"type": "image_url",
                 "image_url": {"url": data_url}},
            ],
        }],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://auremcto.com",
        "X-Title": "AUREM Dev - upload/convert (image)",
    }
    try:
        async with httpx.AsyncClient(timeout=45.0) as c:
            r = await c.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers, json=payload,
            )
            if r.status_code != 200:
                logger.warning("vision call HTTP %s — body: %s",
                                r.status_code, r.text[:300])
                return ""
            data = r.json()
            return (data.get("choices", [{}])[0]
                        .get("message", {})
                        .get("content") or "").strip()
    except Exception as e:
        logger.exception("vision call failed: %r", e)
        return ""


@router.post("/convert")
async def upload_convert(
    file: UploadFile = File(...),
    authorization: Optional[str] = Header(None),
) -> dict:
    """Read an uploaded file, convert it to Markdown, and return the
    markdown body to the frontend so it can be appended to the chat
    prompt as clean LLM-readable text."""
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

    suffix = Path(file.filename or "").suffix.lower() or ""
    ctype = (file.content_type or "").lower()

    # ── Image branch — bypass MarkItDown, use vision LLM ───────────────
    if suffix in IMAGE_EXTS or ctype in IMAGE_MIMES:
        description = await _describe_image_via_vision(
            raw, ctype, file.filename or "image",
        )
        if not description:
            # Vision call failed AND we have no other text to fall back
            # on. Still return success with a placeholder so the
            # frontend's pill shows the attachment instead of going
            # blank. The chat LLM at least sees "user attached an image"
            # rather than nothing.
            description = (
                "_(The user attached an image but vision OCR is "
                "unavailable right now. Ask them to paste any visible "
                "text or describe what they see in the image.)_"
            )
        text = description.strip()
        if len(text) > MAX_MD_CHARS:
            text = text[:MAX_MD_CHARS] + "\n\n... [truncated by server cap]"
        return {
            "ok":            True,
            "kind":          "image",
            "filename":      file.filename or "image",
            "content_type":  ctype,
            "original_size": size,
            "md_size":       len(text),
            "truncated":     False,
            "markdown":      text,
        }

    # ── Document branch — MarkItDown ───────────────────────────────────
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
            raise HTTPException(
                500, "MarkItDown library not installed on server",
            )
        except Exception as e:
            logger.exception(
                "markitdown convert failed for %r", file.filename,
            )
            raise HTTPException(415, f"Couldn't convert this file: {e}")

    text = (getattr(result, "text_content", None) or "").strip()
    if not text:
        # No usable text. Don't 415 — return a clear placeholder so the
        # frontend pill still shows the attachment and the chat LLM at
        # least knows something was sent. Better than silent failure.
        text = (
            f"_(The user uploaded **{file.filename or 'a file'}** but "
            f"the server couldn't extract any readable text from it. "
            f"Ask them what they wanted you to do with it.)_"
        )

    truncated = False
    if len(text) > MAX_MD_CHARS:
        text = text[:MAX_MD_CHARS] + "\n\n... [truncated by server cap]"
        truncated = True

    return {
        "ok":            True,
        "kind":          "doc",
        "filename":      file.filename or "upload",
        "content_type":  ctype,
        "original_size": size,
        "md_size":       len(text),
        "truncated":     truncated,
        "markdown":      text,
    }
