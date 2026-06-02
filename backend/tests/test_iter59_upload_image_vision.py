"""
tests/test_iter59_upload_image_vision.py
==========================================

Iter 59 — Route fix for the user complaint:
"upload feature in chat fails — system uploads file but doesn't read it
 and shows blank."

Root cause (production): images were going through MarkItDown, which
returns no text without OCR set up, so the endpoint raised 415. The
frontend then dropped the toast and the textarea stayed empty.

Fix:
  - Images now bypass MarkItDown entirely and go through an OpenRouter
    vision model (Gemini 2.5 Flash Lite) for description + OCR.
  - Document-path failures no longer raise 415 — they return a clear
    placeholder so the chat LLM at least sees an attachment.
  - Frontend renders attachments as visible pills (separate from the
    textarea) so the user always knows what was uploaded.
  - Drag-and-drop + clipboard-paste attachments.
"""
from __future__ import annotations
import os
import io
import inspect


def _read(rel: str) -> str:
    path = os.path.join(os.path.dirname(__file__), "..", rel)
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# ─── Backend: image branch + vision routing ─────────────────────────────

def test_upload_router_has_image_branch():
    """The endpoint must check for image MIME / extension BEFORE
    MarkItDown — otherwise images go through the no-OCR path and 415."""
    src = _read("routers/upload.py")
    assert "IMAGE_EXTS" in src
    assert "IMAGE_MIMES" in src
    assert "_describe_image_via_vision" in src
    # Branch must be reached before the MarkItDown converter.
    body_start = src.find("async def upload_convert")
    assert body_start > 0
    branch_pos = src.find("suffix in IMAGE_EXTS", body_start)
    mid_pos    = src.find("from markitdown import MarkItDown", body_start)
    assert 0 < branch_pos < mid_pos, (
        "image branch must run before MarkItDown — otherwise images "
        "still hit the no-OCR path."
    )


def test_vision_caller_is_async_with_correct_kwargs():
    from routers.upload import _describe_image_via_vision
    assert inspect.iscoroutinefunction(_describe_image_via_vision)
    sig = inspect.signature(_describe_image_via_vision)
    for kw in ("raw", "content_type", "filename"):
        assert kw in sig.parameters


def test_vision_uses_data_url_format():
    """The model expects `data:<mime>;base64,<...>` for OpenRouter
    vision input. Mistyping this silently returns 'no image found'."""
    src = _read("routers/upload.py")
    assert 'f"data:{mime};base64,{b64}"' in src
    assert '"type": "image_url"' in src


def test_vision_failure_returns_placeholder_not_415():
    """Even if vision fails, the endpoint must NOT 415 — it must return
    a placeholder so the frontend pill stays visible. The 415 silent-
    blank-textarea bug is what the user was hitting."""
    src = _read("routers/upload.py")
    # Slice the source between the "Image branch" comment and the
    # "Document branch" comment — that's the entire image-handling
    # region we need to guarantee never raises.
    parts = src.split("Document branch")
    assert len(parts) >= 2, "could not locate document branch marker"
    image_section = parts[0].split("Image branch")[-1]
    assert "HTTPException" not in image_section, (
        "image branch must not raise — every image attempt should "
        "produce a 200 with a usable markdown body."
    )
    # And the placeholder text must mention vision being unavailable.
    # The literal is split across lines in source so we look for the
    # adjacent token instead of a single string.
    assert "unavailable right now" in src


def test_doc_branch_no_longer_raises_on_empty_text():
    """The old 415 'MarkItDown returned no readable content' branch was
    the same blank-screen bug for unsupported document types. We now
    return a placeholder instead."""
    src = _read("routers/upload.py")
    # The raise we removed must be gone.
    assert 'raise HTTPException(415, "MarkItDown returned no readable content")' not in src
    # And the new placeholder phrasing must be present.
    assert "couldn't extract any readable text" in src


# ─── Frontend: visible attachment pills + drop/paste ───────────────────

def test_chat_panel_renders_attachment_pills():
    src = _read("../frontend/src/components/ChatPanel.jsx")
    # Pill row test-id is what E2E latches onto.
    assert "chat-attachments-row" in src
    # Pill testids are templated: `chat-attach-pill-${a.status}`.
    assert "chat-attach-pill-${a.status}" in src
    # Remove button per pill so user controls the list.
    assert "chat-attach-remove-" in src


def test_chat_panel_send_handles_attachment_only():
    """Image-only chats used to be blocked by `if (!text)`. Iter 59
    allows send when attachments are present even without text."""
    src = _read("../frontend/src/components/ChatPanel.jsx")
    # The send guard must consider both text AND attachments.
    assert "readyAttachments" in src
    assert "!text && !readyAttachments.length" in src


def test_chat_panel_supports_drop_and_paste():
    src = _read("../frontend/src/components/ChatPanel.jsx")
    # Drop wired on the form element.
    assert "onDragOver" in src and "onDrop=" in src
    # Paste wired on the textarea so screenshot pastes work.
    assert "onPaste" in src
    # Paste must read clipboardData.items for the file path (not just
    # text — Cmd-V on a screenshot lands as a File item).
    assert "clipboardData" in src and "kind === \"file\"" in src
