"""Iter 336b — probe-burst serialization + npm guard locks.

Prod: /health flapped EXACTLY at integration_health cron fires
(t=150 s, then every 600 s) — the concurrent 11-probe burst starved
the event loop past nginx's 1 s /health timeout on the 500m-CPU pod.
Plus `npm install -g eslint` raised FileNotFoundError every boot
(no npm on the prod base image).
"""
import asyncio
import time
from pathlib import Path
from unittest.mock import patch

from services import integration_health as ih

CRON_SRC = Path(
    "/app/backend/services/integration_health_cron.py").read_text()
MAIN_SRC = Path("/app/backend/main.py").read_text()


class TestSerialProbes:
    async def test_serial_runs_one_at_a_time_with_gaps(self):
        active = {"now": 0, "peak": 0, "calls": 0}

        async def fake_probe():
            active["now"] += 1
            active["peak"] = max(active["peak"], active["now"])
            active["calls"] += 1
            await asyncio.sleep(0.01)
            active["now"] -= 1
            return {"status": "ok"}

        fake_probes = [(f"p{i}", f"P{i}", fake_probe) for i in range(4)]
        with patch.object(ih, "_PROBES", fake_probes):
            t0 = time.monotonic()
            results = await ih.run_all_probes_serial(gap_s=0.05)
            elapsed = time.monotonic() - t0
        assert active["peak"] == 1, "probes must never overlap"
        assert active["calls"] == 4 and len(results) == 4
        assert elapsed >= 0.2, "yield gap between probes must exist"

    def test_cron_uses_serial_not_concurrent(self):
        assert "run_all_probes_serial" in CRON_SRC
        seg = CRON_SRC.split("async def _probe_and_persist_once")[1]
        seg = seg.split("async def ")[0]
        assert "run_all_probes_serial" in seg
        assert "await run_all_probes()" not in seg

    def test_concurrent_variant_still_exists_for_admin(self):
        assert hasattr(ih, "run_all_probes")


class TestNpmGuard:
    def test_eslint_install_guarded_on_missing_npm(self):
        seg = MAIN_SRC.split('if "eslint" in missing_before:')[1][:600]
        assert 'shutil.which("npm")' in seg
        assert "skipped — npm not present" in seg
