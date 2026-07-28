"""Iter 331 · #3-b — ORA conversational-learning callsite lock.

The council-log + brain-update block in chat_stream must accept the
casual-gateway/advisor result label `mode="chat"` (conversational) while
keeping Mode D/E excluded (BUG 5 — debug/audit replies poison the
fine-tuning corpus)."""
import re
from pathlib import Path

SRC = Path("/app/backend/routers/chat.py").read_text(encoding="utf-8")


def test_mode_filter_accepts_chat_label():
    assert re.search(
        r'_classified_mode in \(None, "A", "B", "chat"\)', SRC), (
        "casual/advisor turns carry mode='chat' — excluding it detaches "
        "log_conversational + update_brain_from_conversation from the "
        "main chat path (proven via live Mongo count freeze at Iter 331)."
    )


def test_mode_filter_still_excludes_debug_and_audit():
    m = re.search(r'_classified_mode in \(([^)]*)\)', SRC)
    assert m, "mode filter tuple missing"
    assert '"D"' not in m.group(1) and '"E"' not in m.group(1), (
        "BUG 5 regression — Mode D/E must never feed ora_council_logs "
        "from the conversational path."
    )


def test_fail_open_logging_present():
    assert SRC.count("ORA shadow-learning") >= 2
