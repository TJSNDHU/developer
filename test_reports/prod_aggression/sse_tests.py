import json, time, requests

BASE = "https://auremcto.com/api/aurem-dev"
TOK = open("/app/test_reports/prod_aggression/token.txt").read().strip()
H = {"Authorization": f"Bearer {TOK}", "Content-Type": "application/json"}
PID = "p_c2b5b8a916"
results = []

def stream_chat(label, payload, timeout=400):
    t0 = time.time()
    rec = {"label": label, "frames": {}, "council": None, "provider": None}
    content_len = 0
    try:
        with requests.post(f"{BASE}/chat/stream", headers=H, json=payload,
                           stream=True, timeout=timeout) as r:
            rec["http"] = r.status_code
            first = None
            for line in r.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue
                if first is None:
                    first = round(time.time() - t0, 1)
                try:
                    ev = json.loads(line[5:].strip())
                except Exception:
                    continue
                et = ev.get("type") or ev.get("event") or "?"
                rec["frames"][et] = rec["frames"].get(et, 0) + 1
                if et == "council":
                    rec["council"] = ev.get("council") or ev
                if et in ("provider", "meta", "done"):
                    rec.setdefault("meta_frames", []).append(json.dumps(ev)[:300])
                    if ev.get("provider"):
                        rec["provider"] = ev.get("provider")
                if et in ("token", "content", "delta"):
                    content_len += len(ev.get("text") or ev.get("content") or "")
                if et == "step":
                    rec.setdefault("steps", []).append((ev.get("text") or "")[:120])
            rec["ttfb_s"] = first
    except Exception as e:
        rec["error"] = str(e)[:200]
    rec["total_s"] = round(time.time() - t0, 1)
    rec["content_len"] = content_len
    return rec

for label, payload in [
    ("council-B-analyze-sse", {"prompt": "Analyze the health of this codebase",
                               "project_id": PID, "mode": "pro", "session_id": "qa-sse-b"}),
    ("ask-advisor-sse", {"prompt": "Review the parliament.py architecture",
                         "project_id": PID, "mode": "pro", "ora_panel": True,
                         "session_id": "qa-sse-adv"}),
]:
    rec = stream_chat(label, payload)
    results.append(rec)
    print(json.dumps(rec, default=str)[:1500], flush=True)
    json.dump(results, open("/app/test_reports/prod_aggression/sse_results.json", "w"), indent=1, default=str)
print("DONE")
