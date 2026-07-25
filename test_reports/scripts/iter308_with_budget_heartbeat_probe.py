"""Runtime probe: LoopEngine._with_budget generic heartbeat emits for all running phases."""
from __future__ import annotations
import asyncio
import json
import sys
sys.path.insert(0, "/app/backend")
from services import loop_engine

class FakeCollection:
    def __init__(self): self.updates=[]; self.inserts=[]
    async def update_one(self, query, update, upsert=False): self.updates.append({"query":query,"update":update,"upsert":upsert})
    async def insert_one(self, doc): self.inserts.append(doc)
    async def find_one(self,*a,**k): return None
class FakeDB:
    def __init__(self):
        self.loop_sessions=FakeCollection(); self.loop_run_log=FakeCollection(); self.loop_events=FakeCollection()

async def run_phase(phase):
    old_interval = loop_engine.HEARTBEAT_INTERVAL_S
    loop_engine.HEARTBEAT_INTERVAL_S = 0.03
    db = FakeDB()
    eng = loop_engine.LoopEngine(db=db, loop_id=f"loop_hb_{phase}", user_id="u", project_id="p", user_message="x")
    canonical = {
        "execute": loop_engine.LoopState.EXECUTING,
        "verify": loop_engine.LoopState.VERIFYING,
        "scan": loop_engine.LoopState.SCANNING,
        "ship": loop_engine.LoopState.SHIPPING,
    }[phase]
    async def slow_coro():
        eng.state = canonical
        eng.phase = phase
        await asyncio.sleep(0.09)
    try:
        await eng._with_budget(phase, slow_coro)
    finally:
        loop_engine.HEARTBEAT_INTERVAL_S = old_interval
    emitted=[]
    while not eng.queue.empty(): emitted.append(await eng.queue.get())
    hbs=[ev for ev in emitted if ev.get("data",{}).get("sub_step")=="heartbeat" and ev.get("data",{}).get("keepalive") is True]
    persisted=[u["update"].get("$set",{}).get("last_event") for u in db.loop_sessions.updates]
    assert hbs, f"no heartbeat emitted for {phase}"
    assert any((ev or {}).get("data",{}).get("sub_step")=="heartbeat" for ev in persisted), f"no persisted heartbeat for {phase}"
    return {"phase": phase, "event_state": hbs[0]["state"], "sub_step": hbs[0]["data"]["sub_step"], "hb_tick": hbs[0]["data"].get("hb_tick")}

async def main():
    out=[]
    for phase in ["execute","verify","scan","ship"]:
        out.append(await run_phase(phase))
    print(json.dumps({"ok": True, "heartbeats": out}, indent=2))

if __name__ == "__main__": asyncio.run(main())
