# Chat-history vanishing bug — Root-cause investigation

> **Status**: Investigation complete. NO patch written. Founder asked
> for diagnosis first, and neither of the two hypotheses in the brief
> ("dual-session-id desync at fetch layer" OR "backend persistence
> gap") is confirmed by the code paths / wire evidence gathered here.
> A THIRD possibility surfaces — needs one specific datapoint from
> production before any code lands.

## TL;DR

1. **Fetch is correct** — every `/chat/history?session_id=…` call
   uses the PROJECT-SCOPED id from `Shell.jsx`, exactly as founder
   observed. Route resolves to `routers/chat.py::chat_history`.
2. **Backend returns `{"messages": [...]}` — frontend reads `messages`.**
   Field names match. Preview curl confirms.
3. **Persistence writes to `chat_sessions.turns[]`** via
   `_persist_turn()` from `/chat/stream` completions. Preview DB
   shows 145 sessions with actual persisted turns.
4. The dual localStorage keys ARE real but are BENIGN — the
   `aurem.chat.sessionId` (generic) key is created by an
   initializer inside `hooks/useChatSession.js` and STAYS UNUSED.
   ChatPanel's fetch and send both use the prop (project-scoped id
   from Shell), not the hook's return value.
5. Given #1–#4 all check out, the empty history observation on
   production is most likely one of:
   - **(a) The specific `/chat/history` call actually returned
     `messages: []` because that project-scoped session genuinely
     has 0 persisted turns in prod Mongo.** Reason unknown from
     source alone — possible causes below.
   - **(b) The response has messages BUT render layer discards them.**
     This is what a code-only audit CANNOT distinguish from (a).
     Need the response body.

## What NOT to do

- Do NOT add a "restore from localStorage" cache fallback.
- Do NOT patch either useChatSession.js field name (`turns` vs
  `messages`) — it's dead code for the render path.
- Do NOT remove the generic `aurem.chat.sessionId` write in the
  same iteration as fixing the actual defect. If we remove it, we
  should be able to prove it wasn't the load-bearing signal for
  anything else. That's a separate cleanup iter after the real
  fix lands.

---

## Wire-level truth (curl evidence)

Preview curl against `http://localhost:8001/api/aurem-dev/chat/history`:

```
Case 1 — unknown session id:
{
    "ok": true,
    "messages": [],
    "session_id": "nonexistent-abc123",
    "title": ""
}

Case 2 — known session id (test-iter212m22-1515517, 6 turns):
ok= True
messages len= 6
first message keys= ['role', 'content', 'ts']
```

So the shape is exactly `{ok, messages, session_id, title}` — the
`messages` field IS the turns array (confirmed by field-key check).
The backend never returns a `turns` field on `/chat/history`.

---

## The dual-session-id story (explained)

Two localStorage keys exist. They come from TWO DIFFERENT SITES:

### Site 1 · `aurem_session_proj_<pid>` (project-scoped)

- Written by `components/Shell.jsx:113-135` inside the
  `[activeProjectId, sessionKeyFor, token]` effect.
- Populated via one of:
  1. localStorage cache (line 113-116),
  2. `/chat/sessions?project_id=<pid>` → adopt server-side session
     (line 120-131),
  3. `newSessionId()` mint (line 134).
- Written value drives `setSessionIdState(next)` → context →
  `Dashboard.jsx:73 useChatSession()` → passed as `sessionId` prop
  into `ChatPanel.jsx:106`.

### Site 2 · `aurem.chat.sessionId` (generic)

- Written by `hooks/useChatSession.js:23-33` inside
  `_readOrCreateSessionId()`.
- Invoked by the hook's `useState` initializer at line 36-38:
  ```js
  const [ownSessionId] = useState(
    () => sessionIdProp || _readOrCreateSessionId()
  );
  ```
- KEY POINT: `_readOrCreateSessionId()` only runs when
  `sessionIdProp` is falsy on the FIRST render of `ChatPanel`. If
  Shell's effect hasn't populated `sessionId` yet (async race with
  server-side adoption), `sessionIdProp` is `null`, so
  `_readOrCreateSessionId()` fires and mints / reads the generic
  key. THAT'S HOW BOTH KEYS END UP IN LOCALSTORAGE.
- Once Shell resolves and sessionIdProp becomes truthy, line 39:
  ```js
  const sessionId = sessionIdProp || ownSessionId;
  ```
  correctly falls back to `sessionIdProp` (project-scoped), and
  the generic-key value in `ownSessionId` STAYS UNUSED for both
  reads and writes.
- Verification: grep proves ChatPanel never uses
  `chatSession.sessionId`, `chatSession.loadHistory`, or
  `chatSession.persistTurn`:
  ```
  grep -n "chatSession\." components/ChatPanel.jsx
  → (no matches)
  ```
- ChatPanel's history load (line 991-1034) uses the prop
  `sessionId` directly for `/chat/history?session_id=<sessionId>`.
- ChatPanel's chat send (line 1507-1517) passes the prop
  `sessionId` directly into `streamChat({...sessionId...})`, which
  becomes `body.session_id` in `POST /chat/stream`.
- Backend `_persist_turn(session_id=body.session_id or "")` writes
  against that same id.

**Conclusion**: the generic key is a benign artifact of first-render
timing. It does NOT influence which id is fetched, which id is
persisted against, or which id renders. Removing it would be
cosmetic. It is NOT the root cause of the vanishing history.

---

## The wrong-field-name bug that ISN'T the bug

There IS a genuine field-name mismatch, but it's dead code:

```js
// hooks/useChatSession.js:63-64
const r = await api.get(`/chat/history?session_id=${sessionId}`);
const turns = r.data?.turns || [];  // ← WRONG: backend returns `messages`
```

vs.

```js
// components/ChatPanel.jsx:997-1000
api.get(`/chat/history`, { params: { session_id: sessionId } })
  .then((r) => {
    const turns = r.data?.messages || [];  // ← CORRECT
```

The `useChatSession.loadHistory` function that carries the wrong
field name is IMPORTED by ChatPanel (line 120) but NEVER INVOKED.
Grep confirms:

```
grep -n "chatSession\.loadHistory\|persistTurn" components/ChatPanel.jsx
→ (no matches)
```

So this is a latent field-name bug that would surface if any
future refactor pointed ChatPanel at the hook's `loadHistory`. It
is NOT what's producing the empty render today.

---

## The remaining unknown (needs founder action)

Given all the above check out at the source level, the most likely
production explanation is **(a) — the specific session on prod
genuinely has zero persisted turns**. Ways this could happen:

1. **Cross-project write:** the founder chatted while
   `aurem_active_project` was set to project X, sessionId minted
   under project X (e.g. `d4bbcbd4` was actually X's session ID).
   Between chat send and reload, `aurem_active_project` flipped to
   Y (perhaps via a Preview-tab open, sidebar click, or auth
   refresh). Reload reads Y's session id, fetches Y's history,
   sees empty. The founder's chat still lives under X.

2. **Persist-side silent failure:** `_persist_turn`
   (`routers/chat.py:608`) catches ALL exceptions and only
   `logger.warning`s. If the update failed (Mongo timeout, index
   error, auth-related motor client swap), the turn was consumed
   into the LLM but never landed on disk. UI showed messages in
   memory during the conversation; reload's fetch returns nothing
   because nothing was ever persisted.

3. **Field-name mismatch on prod ONLY:** if the prod build shipped
   a version of ChatPanel that reads `r.data.turns` (matching the
   legacy hook), an old bundle in the CDN would render empty even
   though the response has messages. Unlikely given commit 7bb304d
   just shipped, but worth ruling out by inspecting the deployed
   bundle.

## What I need from the founder (one message)

Please paste, from a fresh reload on production while the bug is
happening:

1. Response body of `GET /chat/history?session_id=d4bbcbd4-3e57-4c69-8ba1-394dfaa06962`
   — full JSON, not just status code. If `messages` array has
   entries → we're in case (b) (render discards; likely bundle
   issue). If `messages: []` → we're in case (a) (persistence /
   session-flip; server-side investigation next).
2. `localStorage.getItem("aurem_active_project")` value at reload.
   (Compare against the project id that owned the session at the
   time of the vanished chat, if you can recall.)
3. Sample of any prod `_persist_turn failed` warnings in the last
   24 h from the backend logs — those directly implicate case (2).

Once we have (1), the fix is 15 minutes. Ordering matters — I
don't want to write a client-side cache fallback that patches the
symptom while the real defect (session flip OR persist-side crash)
keeps eating turns.

---

## Files inspected for this RCA (no edits)

- `/app/frontend/src/hooks/useChatSession.js`
- `/app/frontend/src/components/Shell.jsx` (lines 40-140)
- `/app/frontend/src/components/ChatPanel.jsx` (lines 106-140,
  985-1040, 1500-1520)
- `/app/frontend/src/pages/Dashboard.jsx` (line 37, 73, 695)
- `/app/frontend/src/lib/api.js` (lines 215-243)
- `/app/backend/routers/chat.py` (lines 557-610, 2918-2947)
- Preview Mongo `chat_sessions` collection (via motor query,
  read-only, no writes)
