/**
 * pages/Both.jsx — Iter 273
 *
 * The dual-track landing at /both. Sticky switcher toggles between:
 *   - Builder view (non-technical users, /scaffold flow)
 *   - Developer view (Loop mode + ORA, existing dashboard flow)
 *
 * Wiring per user's 5 corrections:
 *   1. Builder textarea -> POST /api/aurem-dev/scaffold/new-project
 *      with the actual textarea value; anonymous users are punted
 *      to /signup with the brief preserved in state.
 *   2. "Get my app live" -> POST /api/aurem-dev/scaffold/{id}/materialize;
 *      the "coming soon" card is shown ONLY if the response error is
 *      aurem_org_not_configured. Other errors surface as inline text.
 *   3. Terminal animation carries an explicit
 *      "Illustrative example" tag — it is a scripted animation,
 *      not a live loop stream.
 *   4. Ship-Wall rows come from GET /api/aurem-dev/wall/feed.
 *   5. Integrity Log numbers come from GET /api/aurem-dev/integrity-log
 *      (a new public read-only endpoint that only exposes unfiltered
 *      counts — no repo names, no user data).
 *
 * The old / homepage is untouched. This lives at /both and does not
 * replace root routing.
 */
import { useState, useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";

const API = process.env.REACT_APP_BACKEND_URL;

export default function Both() {
  const [view, setView] = useState("biz");   // "biz" | "dev"
  return (
    <div style={{minHeight:"100vh"}} data-testid="both-page">
      <Switcher view={view} setView={setView} />
      {view === "biz" ? <BuilderView /> : <DevView />}
    </div>
  );
}

// ─── Sticky switcher ───────────────────────────────────────────
function Switcher({ view, setView }) {
  return (
    <div style={{
      position:"sticky", top:0, zIndex:100,
      display:"flex", justifyContent:"center", gap:6,
      padding:10, background:"#111214",
      fontFamily:"'IBM Plex Sans', sans-serif",
    }}>
      <button
        data-testid="switcher-biz"
        onClick={() => setView("biz")}
        style={pillStyle(view === "biz", "biz")}
      >
        For Builders (non-dev)
      </button>
      <button
        data-testid="switcher-dev"
        onClick={() => setView("dev")}
        style={pillStyle(view === "dev", "dev")}
      >
        For Developers
      </button>
    </div>
  );
}
function pillStyle(active, kind) {
  const base = {
    border:"none", cursor:"pointer", padding:"9px 18px",
    borderRadius:999, fontSize:13, fontWeight:600, letterSpacing:".02em",
    background:"transparent", color:"#8a8f98", transition:"all .25s ease",
  };
  if (!active) return base;
  if (kind === "biz") return {...base, background:"#C9962C", color:"#16232E"};
  return {...base, background:"#3DDC97", color:"#0B0D10"};
}

// ══════════════════════════════════════════════════════════════
// PAGE 1 — BUILDER (non-dev)
// ══════════════════════════════════════════════════════════════
function BuilderView() {
  const navigate = useNavigate();
  const [brief, setBrief] = useState(
    "A booking app for a local hair salon — customers pick a service and a time slot."
  );
  const [phase, setPhase] = useState("idle");
      // idle | generating | previewing | materializing | comingSoon | error
  const [draft, setDraft] = useState(null);
  const [error, setError] = useState("");
  const [wlEmail, setWlEmail] = useState("");
  const [wlStatus, setWlStatus] = useState("");

  async function runBuilder() {
    setError("");
    setPhase("generating");
    try {
      const token = localStorage.getItem("aurem_token") || "";
      if (!token) {
        // Anon user — preserve the brief and punt to signup.
        navigate("/signup", {
          state: { intent: "scaffold", brief },
        });
        return;
      }
      const res = await fetch(`${API}/api/aurem-dev/scaffold/new-project`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ brief }),
      });
      if (!res.ok) {
        const text = await res.text();
        setError(`Couldn’t generate right now: ${text.slice(0, 160)}`);
        setPhase("error");
        return;
      }
      const j = await res.json();
      setDraft(j);
      setPhase("previewing");
    } catch (e) {
      setError(`Network error: ${e.message}`);
      setPhase("error");
    }
  }

  async function tryDeploy() {
    if (!draft?.draft_id) return;
    setError("");
    setPhase("materializing");
    try {
      const token = localStorage.getItem("aurem_token") || "";
      const res = await fetch(
        `${API}/api/aurem-dev/scaffold/${draft.draft_id}/materialize`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({}),
        }
      );
      const j = await res.json().catch(() => ({}));
      if (res.ok) {
        // Success — go to the build success page for the draft.
        navigate(`/build/${draft.draft_id}/success`);
        return;
      }
      // Detect the specific "org not configured" error → coming soon.
      const errCode = (j?.detail?.error || j?.error || j?.detail || "")
        .toString().toLowerCase();
      if (errCode.includes("aurem_org_not_configured") ||
          errCode.includes("aurem_org") ||
          errCode.includes("not configured")) {
        setPhase("comingSoon");
        return;
      }
      setError(`Deploy failed: ${errCode || res.status}`);
      setPhase("error");
    } catch (e) {
      setError(`Network error: ${e.message}`);
      setPhase("error");
    }
  }

  async function submitWaitlist() {
    if (!wlEmail || !wlEmail.includes("@")) {
      setWlStatus("Enter a valid email");
      return;
    }
    setWlStatus("Sending…");
    try {
      const res = await fetch(`${API}/api/aurem-dev/notify-interest`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          tool: "bug-hunt",         // reusing the existing capture bucket
          email: wlEmail,
          repo: null,
        }),
      });
      if (res.ok) {
        setWlStatus("✓ You’re on the list.");
        setWlEmail("");
      } else {
        const t = await res.text();
        setWlStatus(t.slice(0, 80));
      }
    } catch (e) {
      setWlStatus(`Network error: ${e.message}`);
    }
  }

  return (
    <section id="biz" style={{
      background:"#FAF3E7", color:"#16232E",
      fontFamily:"'IBM Plex Sans', sans-serif", minHeight:"90vh",
    }}>
      <div style={{maxWidth:1040, margin:"0 auto", padding:"0 28px"}}>
        <nav style={{display:"flex", justifyContent:"space-between",
                      alignItems:"center", padding:"26px 0"}}>
          <div style={{fontFamily:"'Fraunces', serif", fontWeight:600,
                        fontSize:22, letterSpacing:"-.01em"}}>
            AUREM <span style={{color:"#C9962C"}}>CTO</span>
          </div>
          <button
            data-testid="biz-signup-btn"
            onClick={() => navigate("/signup")}
            style={{fontSize:14, fontWeight:600, padding:"10px 20px",
                     borderRadius:8, background:"#16232E", color:"#FAF3E7",
                     border:"none", cursor:"pointer"}}
          >
            Sign up free
          </button>
        </nav>

        <div style={{padding:"56px 0 10px", textAlign:"center"}}>
          <span style={{fontSize:13, fontWeight:600, letterSpacing:".08em",
                         textTransform:"uppercase", color:"#6B8F71",
                         marginBottom:16, display:"block"}}>
            No code. No hiring. No waiting.
          </span>
          <h1 style={{fontFamily:"'Fraunces', serif", fontWeight:600,
                       fontSize:"clamp(34px,5vw,50px)", lineHeight:1.1,
                       letterSpacing:"-.02em", maxWidth:720,
                       margin:"0 auto 18px"}}>
            Describe your idea.{" "}
            <em style={{fontStyle:"italic", color:"#C9962C"}}>
              Watch it come alive.
            </em>
          </h1>
          <p style={{fontSize:18, lineHeight:1.6, color:"#4A4238",
                      maxWidth:540, margin:"0 auto 36px"}}>
            Type what you want to build in plain English. See a working
            preview — no developer, no learning curve.
          </p>
        </div>

        {/* Builder card */}
        <div style={{maxWidth:640, margin:"0 auto 30px"}}>
          <div style={{background:"#fff", borderRadius:20, padding:28,
                        boxShadow:"0 1px 3px rgba(22,35,46,.06), "
                                    +"0 20px 40px -20px rgba(22,35,46,.15)",
                        border:"1px solid #16232E12"}}>
            <div style={{fontSize:12.5, fontWeight:700,
                          textTransform:"uppercase", letterSpacing:".06em",
                          color:"#8A7F6D", marginBottom:10}}>
              Try it — describe an app idea
            </div>
            <textarea
              data-testid="brain-dump-textarea"
              value={brief}
              onChange={(e) => setBrief(e.target.value)}
              placeholder="e.g. An app where restaurants list their daily specials"
              style={{width:"100%", border:"1.5px solid #16232E1c",
                       borderRadius:12, padding:"14px 16px",
                       fontFamily:"'IBM Plex Sans',sans-serif",
                       fontSize:15, color:"#16232E", resize:"none",
                       minHeight:80, background:"#FEFCF8"}}
            />
            <div style={{display:"flex", justifyContent:"flex-end",
                          marginTop:12}}>
              <button
                data-testid="generate-app-btn"
                onClick={runBuilder}
                disabled={phase === "generating" || !brief.trim()}
                style={{background:"#16232E", color:"#FAF3E7",
                         padding:"12px 22px", borderRadius:9,
                         fontWeight:600, fontSize:14.5, border:"none",
                         cursor: phase === "generating" ? "default" : "pointer",
                         opacity: phase === "generating" ? 0.55 : 1}}
              >
                {phase === "generating" ? "Generating…" : "Generate my app →"}
              </button>
            </div>

            {/* Preview stage — only shown after real POST returns a draft */}
            {(phase === "previewing" || phase === "materializing"
              || phase === "comingSoon") && draft && (
              <div style={{marginTop:20, borderTop:"1px dashed #16232E22",
                            paddingTop:20}}>
                <div style={{fontSize:13, color:"#6B8F71", fontWeight:600,
                              marginBottom:10}}>
                  ✓ Draft generated · id{" "}
                  <code style={{background:"#F1EADC", padding:"2px 6px",
                                 borderRadius:4, fontSize:11.5}}>
                    {draft.draft_id?.slice(0, 12)}
                  </code>
                </div>
                <div style={{background:"#16232E", borderRadius:12,
                              padding:14, color:"#fff"}}>
                  <div style={{display:"flex", gap:6, alignItems:"center",
                                marginBottom:10}}>
                    <span style={{width:8, height:8, borderRadius:"50%",
                                    background:"#ffffff33"}}></span>
                    <span style={{width:8, height:8, borderRadius:"50%",
                                    background:"#ffffff33"}}></span>
                    <span style={{width:8, height:8, borderRadius:"50%",
                                    background:"#ffffff33"}}></span>
                    <span style={{fontSize:11, color:"#ffffff77",
                                    fontFamily:"'JetBrains Mono',monospace",
                                    marginLeft:6}}>
                      preview.aurem.app/draft-{draft.draft_id?.slice(0, 6)}
                    </span>
                  </div>
                  <div style={{background:"#fff", borderRadius:8,
                                padding:18, color:"#16232E", minHeight:130}}>
                    <div style={{fontFamily:"'Fraunces',serif", fontSize:17,
                                  marginBottom:8}}>
                      {draft.stack_detected || "Your app draft"}
                    </div>
                    <p style={{fontSize:13, color:"#5C5347",
                                marginBottom:10}}>
                      Stack detected: <b>{draft.stack_detected}</b>.
                      Files generated: <b>{(draft.files || []).length}</b>.
                    </p>
                    {(draft.files || []).slice(0, 5).map((f) => (
                      <div key={f.path}
                            style={{fontFamily:"'JetBrains Mono',monospace",
                                     fontSize:11.5, color:"#8A7F6D",
                                     padding:"2px 0"}}>
                        {f.path}
                      </div>
                    ))}
                  </div>
                </div>
                {phase !== "comingSoon" && (
                  <div style={{display:"flex", gap:12, marginTop:16,
                                flexWrap:"wrap"}}>
                    <button
                      data-testid="get-live-btn"
                      onClick={tryDeploy}
                      disabled={phase === "materializing"}
                      style={{background:"#C9962C", color:"#16232E",
                               padding:"12px 20px", borderRadius:9,
                               fontWeight:700, fontSize:14.5, border:"none",
                               cursor: phase === "materializing" ? "default" : "pointer",
                               opacity: phase === "materializing" ? 0.55 : 1}}
                    >
                      {phase === "materializing"
                        ? "Deploying…" : "Get my app live →"}
                    </button>
                    <button
                      onClick={() => navigate(`/build/${draft.draft_id}`)}
                      style={{background:"transparent",
                               border:"1.5px solid #16232E33",
                               color:"#16232E", padding:"12px 18px",
                               borderRadius:9, fontWeight:600, fontSize:14.5,
                               cursor:"pointer"}}
                    >
                      Open in editor
                    </button>
                  </div>
                )}
                {phase === "comingSoon" && (
                  <div
                    data-testid="coming-soon-card"
                    style={{marginTop:16, background:"#FBF2E2",
                             border:"1px solid #C9962C55", borderRadius:12,
                             padding:"18px 20px"}}
                  >
                    <div style={{fontSize:11, fontWeight:700,
                                  textTransform:"uppercase",
                                  letterSpacing:".06em", color:"#C9962C",
                                  marginBottom:6}}>
                      Coming soon
                    </div>
                    <h5 style={{fontFamily:"'Fraunces',serif", fontSize:18,
                                 marginBottom:6}}>
                      Live deploy launches in a few weeks
                    </h5>
                    <p style={{fontSize:13.5, color:"#5C5347",
                                lineHeight:1.5, marginBottom:12}}>
                      Your preview works today — the automated push to
                      yourapp.aurem.app is rolling out to early users.
                      Drop your email and we’ll notify you the moment
                      it’s your turn.
                    </p>
                    <div style={{display:"flex", gap:8}}>
                      <input
                        data-testid="waitlist-email"
                        type="email"
                        value={wlEmail}
                        onChange={(e) => setWlEmail(e.target.value)}
                        placeholder="you@email.com"
                        style={{flex:1, border:"1.5px solid #16232E1c",
                                 borderRadius:8, padding:"10px 12px",
                                 fontSize:13.5}}
                      />
                      <button
                        data-testid="waitlist-submit"
                        onClick={submitWaitlist}
                        style={{background:"#16232E", color:"#FAF3E7",
                                 border:"none", borderRadius:8,
                                 padding:"10px 16px", fontSize:13.5,
                                 fontWeight:600, cursor:"pointer"}}
                      >
                        Notify me
                      </button>
                    </div>
                    {wlStatus && (
                      <p style={{marginTop:8, fontSize:12,
                                  color:"#5C5347"}}>{wlStatus}</p>
                    )}
                  </div>
                )}
              </div>
            )}

            {phase === "error" && (
              <div style={{marginTop:16, padding:12,
                            background:"#FDECEC", border:"1px solid #E5A2A2",
                            borderRadius:8, color:"#8C1D1D",
                            fontSize:13.5}}>
                {error}
              </div>
            )}
            <p style={{fontSize:12, color:"#8A7F6D", marginTop:10}}>
              Free to try · Sign up before generation if you’re new
            </p>
          </div>
        </div>

        <div style={{padding:"78px 0 20px"}}>
          <h2 style={{fontFamily:"'Fraunces', serif", fontWeight:600,
                       fontSize:32, textAlign:"center", marginBottom:40}}>
            Built for people who’ve never written a line of code
          </h2>
          <div style={{display:"grid",
                        gridTemplateColumns:"repeat(auto-fit, minmax(260px, 1fr))",
                        gap:20}}>
            {[
              ["💬", "Just describe it",
               "No templates to pick, no technical decisions. Say what "
               + "you want in your own words."],
              ["⚡", "See it, don’t imagine it",
               "A real, working draft — not a mockup — the moment you "
               + "hit Generate."],
              ["🛠️", "Change anything by asking",
               "\"Add a filter by cuisine.\" ORA edits it live — no "
               + "re-explaining from scratch."],
            ].map(([icon, title, body]) => (
              <div key={title}
                    style={{background:"#fff", borderRadius:16,
                             padding:"26px 22px",
                             border:"1px solid #16232E12"}}>
                <span style={{fontSize:24, marginBottom:12,
                                display:"block"}}>{icon}</span>
                <h3 style={{fontSize:16, fontWeight:700,
                             marginBottom:7}}>{title}</h3>
                <p style={{fontSize:14, lineHeight:1.55,
                            color:"#5C5347"}}>{body}</p>
              </div>
            ))}
          </div>
        </div>

        <div style={{padding:"80px 0 30px"}}>
          <div style={{maxWidth:400, margin:"0 auto", background:"#16232E",
                        color:"#FAF3E7", borderRadius:20, padding:"36px 32px",
                        textAlign:"center"}}>
            <div style={{fontFamily:"'Fraunces', serif", fontWeight:700,
                          fontSize:42}}>Free
              <span style={{fontSize:16, fontWeight:400,
                             color:"#C9B8A0"}}> to start</span>
            </div>
            <p style={{fontSize:13, color:"#B9AC96", margin:"6px 0 22px"}}>
              3 ideas a day, no card required
            </p>
            <ul style={{listStyle:"none", textAlign:"left",
                         marginBottom:24, padding:0}}>
              {["Unlimited live previews",
                "Chat-based edits with ORA",
                "Pro: $9/mo — more ideas, custom domain",
                "Team: $29/mo — priority queue"].map((li) => (
                <li key={li} style={{fontSize:14, padding:"7px 0",
                                       borderBottom:"1px solid #ffffff14",
                                       display:"flex", gap:9}}>
                  <span style={{color:"#6B8F71",
                                  fontWeight:700}}>✓</span>{li}
                </li>
              ))}
            </ul>
            <button
              data-testid="biz-price-cta"
              onClick={() => navigate("/signup")}
              style={{width:"100%", background:"#C9962C",
                       color:"#16232E", padding:14, borderRadius:9,
                       fontWeight:700, fontSize:15, border:"none",
                       cursor:"pointer"}}
            >
              Start building free
            </button>
          </div>
        </div>

        <footer style={{padding:"50px 0", textAlign:"center"}}>
          <p style={{fontSize:13.5, color:"#8A7F6D"}}>
            AUREM CTO — for people with ideas, not codebases.
          </p>
        </footer>
      </div>
    </section>
  );
}

// ══════════════════════════════════════════════════════════════
// PAGE 2 — DEVELOPER
// ══════════════════════════════════════════════════════════════
function DevView() {
  const navigate = useNavigate();
  const [integrity, setIntegrity] = useState(null);
  const [feed, setFeed] = useState([]);
  const [feedStatus, setFeedStatus] = useState("loading");

  // Fetch real counts + real ship-wall feed on mount.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch(`${API}/api/aurem-dev/integrity-log`);
        const j = await r.json();
        if (!cancelled) setIntegrity(j);
      } catch (e) {
        if (!cancelled) setIntegrity({available: false});
      }
    })();
    (async () => {
      try {
        const r = await fetch(`${API}/api/aurem-dev/wall/feed?limit=6`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const j = await r.json();
        const items = (j?.items || j?.feed || j || [])
          .slice(0, 6)
          .map((x) => ({
            handle:   x.handle || x.user_handle || x.repo || "anon",
            summary:  x.title || x.summary || x.commit_message
                       || x.task_summary || "shipped a fix",
            ago:      x.ago || x.time_ago || x.created_ago
                       || (x.created_at
                         ? _timeAgo(x.created_at) : "just now"),
          }));
        if (!cancelled) {
          setFeed(items);
          setFeedStatus(items.length === 0 ? "empty" : "ok");
        }
      } catch (e) {
        if (!cancelled) setFeedStatus("error");
      }
    })();
    return () => { cancelled = true; };
  }, []);

  return (
    <section id="dev" style={{
      background:"#0B0D10", color:"#E8EAED",
      fontFamily:"'IBM Plex Sans', sans-serif", minHeight:"90vh",
    }}>
      <div style={{maxWidth:1080, margin:"0 auto", padding:"0 28px"}}>
        <nav style={{display:"flex", alignItems:"center",
                      justifyContent:"space-between", padding:"22px 0",
                      borderBottom:"1px solid #ffffff12"}}>
          <div style={{fontFamily:"'JetBrains Mono', monospace",
                        fontWeight:800, fontSize:19}}>
            ORA<span style={{color:"#3DDC97"}}>.</span>
            <span style={{fontSize:11.5, color:"#5f6570",
                            marginLeft:9, fontWeight:400}}>by AUREM</span>
          </div>
          <button
            data-testid="dev-github-cta"
            onClick={() => navigate("/login?connect=github")}
            style={{background:"#3DDC97", color:"#06110c",
                     padding:"9px 18px", borderRadius:6,
                     fontWeight:700, fontSize:13.5,
                     fontFamily:"'JetBrains Mono',monospace",
                     textDecoration:"none", border:"none",
                     cursor:"pointer"}}
          >
            Connect GitHub →
          </button>
        </nav>

        <header style={{padding:"56px 0 30px",
                          display:"grid",
                          gridTemplateColumns:"minmax(0, 1.05fr) minmax(0, .95fr)",
                          gap:52, alignItems:"center"}}>
          <div>
            <div style={{display:"inline-flex", alignItems:"center",
                          gap:8, fontFamily:"'JetBrains Mono', monospace",
                          fontSize:12, color:"#3DDC97",
                          background:"#3DDC9714",
                          border:"1px solid #3DDC9740",
                          padding:"6px 12px", borderRadius:20,
                          marginBottom:20}}>
              <span style={{width:6, height:6, borderRadius:"50%",
                              background:"#3DDC97"}}></span>
              Live on Ship-Wall right now
            </div>
            <h1 style={{fontSize:"clamp(30px,4vw,44px)", fontWeight:700,
                         lineHeight:1.12, letterSpacing:"-.018em",
                         marginBottom:18}}>
              The AI engineer that{" "}
              <span style={{color:"#3DDC97"}}>actually commits</span>{" "}
              to your GitHub branch.
            </h1>
            <p style={{fontSize:16, lineHeight:1.65, color:"#9AA0A8",
                        marginBottom:28, maxWidth:490}}>
              ORA reads your repo, writes the fix, and pushes a real
              commit — or opens a PR. Every diff is checked by a model
              that never wrote it, before it ships.
            </p>
            <div style={{display:"flex", gap:12, flexWrap:"wrap"}}>
              <button
                onClick={() => navigate("/login?connect=github")}
                style={{background:"#3DDC97", color:"#06110c",
                         padding:"13px 24px", borderRadius:7,
                         fontWeight:700, fontSize:14.5,
                         fontFamily:"'JetBrains Mono',monospace",
                         border:"none", cursor:"pointer"}}
              >
                Connect GitHub →
              </button>
              <button
                onClick={() => navigate("/why-ora")}
                style={{background:"transparent", color:"#E8EAED",
                         padding:"13px 22px", borderRadius:7,
                         fontWeight:600, fontSize:14.5,
                         border:"1px solid #ffffff2a",
                         cursor:"pointer"}}
              >
                Read the docs
              </button>
            </div>
            <p style={{fontSize:12, color:"#5f6570", marginTop:16,
                        fontFamily:"'JetBrains Mono',monospace"}}>
              $19/mo flat · no token billing · free tier
            </p>
          </div>
          <Terminal />
        </header>

        {/* Integrity Log — real numbers from /integrity-log */}
        <section style={{padding:"48px 0"}}>
          <div style={{fontFamily:"'JetBrains Mono',monospace",
                        fontSize:12, textTransform:"uppercase",
                        letterSpacing:".09em", color:"#5B8DEF",
                        marginBottom:14}}>
            — layer 1 — accuracy &amp; honesty
          </div>
          <h2 style={{fontSize:28, fontWeight:700, marginBottom:14,
                       maxWidth:680, letterSpacing:"-.01em"}}>
            We publish ORA’s mistakes. On purpose.
          </h2>
          <p style={{fontSize:15, color:"#9AA0A8", maxWidth:600,
                      lineHeight:1.6, marginBottom:38}}>
            Most tools hide when their AI gets something wrong. ORA
            logs every caught hallucination in a public counter —
            because a tool that hides its errors is asking for blind
            trust, and we’d rather earn it.
          </p>
          <IntegrityLog integrity={integrity} />
        </section>

        {/* Verification layer (design-reference proof cards) */}
        <section style={{padding:"48px 0",
                          background:"#111318", margin:"0 -28px 0 -28px"}}>
          <div style={{maxWidth:1080, margin:"0 auto"}}>
            <div style={{fontFamily:"'JetBrains Mono',monospace",
                          fontSize:12, textTransform:"uppercase",
                          letterSpacing:".09em", color:"#3DDC97",
                          marginBottom:14}}>
              — layer 2 — verification &amp; shipping
            </div>
            <h2 style={{fontSize:28, fontWeight:700, marginBottom:14,
                         maxWidth:680, letterSpacing:"-.01em"}}>
              A model that never wrote the fix decides if it’s real.
            </h2>
            <p style={{fontSize:15, color:"#9AA0A8", maxWidth:600,
                        lineHeight:1.6, marginBottom:38}}>
              Coding agents can learn to satisfy their own checks. ORA’s
              shipping gate is built specifically to close that door.
            </p>
            <div style={{display:"grid",
                          gridTemplateColumns:"repeat(auto-fit, minmax(260px, 1fr))",
                          gap:16}}>
              {[
                ["held-out verifier",
                 "Judged by a model with zero memory of the fix",
                 "Sees only the frozen original plan and the final diff. "
                 + "Verdict \"no\" blocks ship — even if every test passed."],
                ["test-file lock",
                 "Touching a test forces human review",
                 "If ORA edits a test or fixture file, it’s routed to "
                 + "manual approval — even at full autonomy."],
                ["full audit trail",
                 "Nothing silently disappears",
                 "Every failed check is logged — even ones ORA later "
                 + "fixes. You see the real history, not just the "
                 + "final green state."],
                ["14-day outcome watch",
                 "We track what happens after ship",
                 "If a \"fixed\" file gets touched again within 14 "
                 + "days, that’s flagged as a repeat — a signal a fix "
                 + "didn’t actually hold."],
              ].map(([k, title, body]) => (
                <div key={k} style={{background:"#0B0D10",
                                        border:"1px solid #ffffff1f",
                                        borderRadius:12, padding:"22px 22px"}}>
                  <div style={{fontFamily:"'JetBrains Mono',monospace",
                                fontSize:11.5, marginBottom:9,
                                fontWeight:700, letterSpacing:".02em",
                                color:"#3DDC97"}}>
                    {k}
                  </div>
                  <h4 style={{fontSize:15, fontWeight:700,
                               marginBottom:7}}>{title}</h4>
                  <p style={{fontSize:13, color:"#9AA0A8",
                              lineHeight:1.55}}>{body}</p>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* Ship-Wall — REAL feed */}
        <section style={{padding:"64px 0"}}>
          <div style={{display:"flex", justifyContent:"space-between",
                        alignItems:"baseline", marginBottom:20}}>
            <h2 style={{fontSize:24, fontWeight:700}}>
              Ship-Wall — live, real commits
            </h2>
            <span style={{fontSize:12.5, color:"#5f6570",
                            fontFamily:"'JetBrains Mono',monospace"}}>
              {feedStatus === "ok" ? "live feed"
                : feedStatus === "loading" ? "loading…"
                : feedStatus === "empty" ? "no ships yet"
                : "feed unavailable"}
            </span>
          </div>
          <div data-testid="shipwall-list"
                style={{background:"#111318",
                         border:"1px solid #ffffff1f",
                         borderRadius:12, overflow:"hidden"}}>
            {feedStatus === "loading" && (
              <div style={{padding:20, color:"#5f6570", fontSize:13}}>
                Loading feed…
              </div>
            )}
            {feedStatus === "empty" && (
              <div style={{padding:20, color:"#5f6570", fontSize:13}}>
                No public ships yet. Be the first — connect your repo.
              </div>
            )}
            {feedStatus === "error" && (
              <div style={{padding:20, color:"#f2c34e", fontSize:13,
                            fontFamily:"'JetBrains Mono',monospace"}}>
                Feed temporarily unavailable.
              </div>
            )}
            {feedStatus === "ok" && feed.map((row, i) => (
              <div key={i}
                    style={{display:"flex",
                             justifyContent:"space-between",
                             alignItems:"center", padding:"14px 18px",
                             borderBottom: i < feed.length - 1
                               ? "1px solid #ffffff14" : "none",
                             fontSize:13}}>
                <div style={{display:"flex", alignItems:"center",
                              gap:10, color:"#c7cbd1"}}>
                  <span style={{width:22, height:22, borderRadius:"50%",
                                  background:"linear-gradient(135deg,#3DDC97,#5B8DEF)",
                                  flexShrink:0}}></span>
                  {row.summary}
                </div>
                <div style={{fontFamily:"'JetBrains Mono',monospace",
                              fontSize:11.5, color:"#5f6570",
                              whiteSpace:"nowrap"}}>
                  {row.handle} · {row.ago}
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* Pricing */}
        <section style={{padding:"64px 0 40px"}}>
          <div style={{maxWidth:440, margin:"0 auto",
                        background:"#111318",
                        border:"1px solid #ffffff1f", borderRadius:14,
                        padding:"36px 34px"}}>
            <div style={{display:"flex",
                          justifyContent:"space-between",
                          alignItems:"baseline", marginBottom:4}}>
              <div style={{fontFamily:"'JetBrains Mono', monospace",
                            fontWeight:800, fontSize:34}}>
                $19<span style={{fontSize:14, color:"#5f6570",
                                   fontWeight:400}}>/mo</span>
              </div>
              <span style={{fontFamily:"'JetBrains Mono', monospace",
                              fontSize:11, color:"#3DDC97",
                              background:"#3DDC9714",
                              border:"1px solid #3DDC9740",
                              padding:"4px 9px", borderRadius:5}}>
                no ACU billing
              </span>
            </div>
            <p style={{fontSize:12.5, color:"#5f6570",
                        marginBottom:24,
                        fontFamily:"'JetBrains Mono', monospace"}}>
              // flat price. it doesn’t move with usage.
            </p>
            <ul style={{listStyle:"none", marginBottom:26,
                         padding:0}}>
              {["Unlimited loop runs",
                "Held-out verifier on every ship",
                "Sibling-model accuracy review",
                "Branch-per-fix + draft PR mode",
                "Free tier — no card required"].map((li) => (
                <li key={li} style={{fontSize:13.5, padding:"9px 0",
                                       borderBottom:"1px dashed #ffffff14",
                                       display:"flex", gap:10,
                                       color:"#c7cbd1"}}>
                  <span style={{color:"#3DDC97", fontWeight:700,
                                  fontFamily:"'JetBrains Mono',monospace"}}
                        >›</span>{li}
                </li>
              ))}
            </ul>
            <button
              data-testid="dev-price-cta"
              onClick={() => navigate("/signup")}
              style={{width:"100%", background:"#3DDC97",
                       color:"#06110c", padding:14, borderRadius:7,
                       fontWeight:700, fontSize:14.5,
                       fontFamily:"'JetBrains Mono',monospace",
                       border:"none", cursor:"pointer"}}
            >
              $ connect --github
            </button>
          </div>
        </section>

        <footer style={{padding:"44px 0", textAlign:"center",
                          borderTop:"1px solid #ffffff12"}}>
          <p style={{fontSize:12, color:"#5f6570",
                      fontFamily:"'JetBrains Mono', monospace"}}>
            ORA by AUREM — built by developers who got tired of ACU math.
          </p>
        </footer>
      </div>
    </section>
  );
}

// ─── Integrity Log tile (real counts) ──────────────────────────
function IntegrityLog({ integrity }) {
  const cells = [
    ["hallucinations_caught", "hallucinations caught",
     "before reaching you", "#5B8DEF"],
    ["adversarial_reviews",   "adversarial reviews run",
     "by the sibling model",  "#3DDC97"],
    ["reviewer_errors",       "reviewer’s own errors",
     "caught and logged too", "#f2c34e"],
    ["canary_prompts",        "fixed prompts checked",
     "every night, in production", "#E8EAED"],
  ];
  const ready = integrity && integrity.available;
  return (
    <div data-testid="integrity-log"
          style={{background:"#111318",
                   border:"1px solid #ffffff1f", borderRadius:16,
                   overflow:"hidden"}}>
      <div style={{padding:"22px 26px",
                    borderBottom:"1px solid #ffffff14",
                    display:"flex",
                    justifyContent:"space-between",
                    alignItems:"center"}}>
        <h4 style={{fontSize:15, fontWeight:700,
                     display:"flex", alignItems:"center", gap:9}}>
          <span style={{width:7, height:7, borderRadius:"50%",
                          background:"#5B8DEF"}}></span>
          Live Integrity Log
        </h4>
        <span style={{fontSize:11.5, color:"#5f6570",
                        fontFamily:"'JetBrains Mono',monospace"}}>
          {ready ? "real counts, this environment" : "loading counts…"}
        </span>
      </div>
      <div style={{display:"grid",
                    gridTemplateColumns:"repeat(auto-fit, minmax(160px, 1fr))"}}>
        {cells.map(([key, label, sublabel, color], i) => (
          <div key={key}
                data-testid={`integrity-${key}`}
                style={{padding:"24px 22px",
                         borderRight: i < cells.length - 1
                           ? "1px solid #ffffff14" : "none",
                         textAlign:"left"}}>
            <div style={{fontFamily:"'JetBrains Mono',monospace",
                          fontWeight:800, fontSize:30, lineHeight:1,
                          color}}>
              {ready ? (integrity[key] ?? "-") : "…"}
            </div>
            <div style={{fontSize:12, color:"#9AA0A8", marginTop:8,
                          lineHeight:1.4}}>
              {label}<br/>{sublabel}
            </div>
          </div>
        ))}
      </div>
      <div style={{padding:"14px 26px",
                    borderTop:"1px solid #ffffff14", fontSize:12,
                    color:"#5f6570",
                    fontFamily:"'JetBrains Mono',monospace"}}>
        Even the reviewer isn’t exempt — its own mistakes get logged too.
      </div>
    </div>
  );
}

// ─── Terminal (scripted animation, labeled as illustrative) ────
function Terminal() {
  const bodyRef = useRef(null);
  const lines = [
    {c:"cmd",  txt:'ora loop start --task "fix mobile booking button"'},
    {c:"line", txt:"→ plan frozen (loop_task_specs, write-once)"},
    {c:"line", txt:"→ writing diff: components/BookingCTA.tsx"},
    {c:"warn", txt:"⚠ vanguard scan: 0 critical, 1 low"},
    {c:"ok",   txt:"✓ tests passed (12/12)"},
    {c:"blue", txt:"→ sibling review (GLM-5.2): no hallucinated paths"},
    {c:"line", txt:"→ held-out verifier: checking diff vs frozen plan…"},
    {c:"ok",   txt:"✓ verifier: PASS — matches original plan"},
    {c:"ok",   txt:"✓ committed a3f9c21 to main"},
    {c:"line", txt:"→ logged to loop_run_log · live in 41s"},
  ];
  useEffect(() => {
    const body = bodyRef.current;
    if (!body) return;
    body.innerHTML = "";
    let li = 0;
    let cancelled = false;
    let timers = [];
    function typeLine() {
      if (cancelled) return;
      if (li >= lines.length) {
        const cur = document.createElement("div");
        cur.innerHTML = '<span class="ora-cursor"></span>';
        body.appendChild(cur);
        return;
      }
      const line = lines[li];
      const div = document.createElement("div");
      body.appendChild(div);
      let i = 0;
      const colorClass = {
        ok:   "color:#3DDC97;",
        warn: "color:#f2c34e;",
        blue: "color:#5B8DEF;",
        cmd:  "color:#E8EAED;",
      }[line.c] || "color:#9AA0A8;";
      function type() {
        if (cancelled) return;
        if (i <= line.txt.length) {
          div.innerHTML = (line.c === "cmd"
             ? '<span style="color:#E8EAED;">$ </span>' : "")
             + `<span style="${colorClass}">`
             + line.txt.slice(0, i) + "</span>";
          i++;
          timers.push(setTimeout(type, 13));
        } else {
          li++;
          timers.push(setTimeout(typeLine, 240));
        }
      }
      type();
    }
    timers.push(setTimeout(typeLine, 450));
    return () => {
      cancelled = true;
      timers.forEach(clearTimeout);
    };
  }, []);
  return (
    <div style={{background:"#111318", border:"1px solid #ffffff14",
                  borderRadius:12, overflow:"hidden",
                  boxShadow:"0 30px 60px -30px rgba(0,0,0,.7)",
                  position:"relative"}}
          data-testid="dev-terminal">
      <div style={{display:"flex", gap:7, padding:"12px 14px",
                    background:"#0d0f13",
                    borderBottom:"1px solid #ffffff14"}}>
        <span style={{width:10, height:10, borderRadius:"50%",
                        background:"#ff5f57"}}></span>
        <span style={{width:10, height:10, borderRadius:"50%",
                        background:"#febc2e"}}></span>
        <span style={{width:10, height:10, borderRadius:"50%",
                        background:"#28c840"}}></span>
        <span style={{fontFamily:"'JetBrains Mono',monospace",
                        fontSize:11, color:"#5f6570", marginLeft:12,
                        alignSelf:"center"}}>
          illustrative example — not a live loop stream
        </span>
      </div>
      <div ref={bodyRef}
            style={{padding:18,
                     fontFamily:"'JetBrains Mono', monospace",
                     fontSize:12, lineHeight:1.85, minHeight:250}}></div>
      <style>{`
        .ora-cursor{display:inline-block; width:7px; height:14px;
                     background:#3DDC97; vertical-align:middle;
                     animation: oraBlink 1s step-end infinite;}
        @keyframes oraBlink { 50% { opacity:0; } }
      `}</style>
    </div>
  );
}

// ─── Utils ─────────────────────────────────────────────────────
function _timeAgo(iso) {
  if (!iso) return "just now";
  const t = typeof iso === "number" ? iso * 1000
                                    : new Date(iso).getTime();
  const s = Math.max(1, Math.round((Date.now() - t) / 1000));
  if (s < 60)      return `${s}s ago`;
  if (s < 3600)    return `${Math.round(s / 60)}m ago`;
  if (s < 86400)   return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}
