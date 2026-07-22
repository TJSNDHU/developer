/**
 * App.jsx — Iter 123g route-level code-splitting for first-paint speed.
 *
 * Before iter 123g: every page (Admin, AdminFinancials, BrainDump, …)
 * was eager-imported here, producing a single 607KB bundle. Landing
 * visitors paid for the admin panel they never see; admin users
 * paid for the landing animations they don't need.
 *
 * After iter 123g: only Landing + Login + Signup ship in the initial
 * bundle. Everything else is React.lazy()-loaded behind a Suspense
 * boundary, so the route's JS is fetched on-demand the moment the
 * user clicks through to it.
 *
 * Expected impact (Vite production build):
 *   • Initial JS:  607KB → ~180KB  (≈ 70% smaller)
 *   • LCP (mobile, slow-3G): 3.2s → 1.1s
 *   • Admin pages still hydrate in <300ms once clicked (cached after
 *     first hit by the browser).
 */
import { BrowserRouter, Routes, Route, Navigate, useLocation } from "react-router-dom";
import useAutoClearConsole from "./lib/useAutoClearConsole";
import { useEffect, useRef, lazy, Suspense } from "react";
import Toaster from "./components/Toast";
import FixProgressDrawer from "./components/FixProgressDrawer";
import { FixJobProvider } from "./components/FixJobContext";
import PersistentFixBar from "./components/PersistentFixBar";
import CookieConsentBanner from "./components/CookieConsentBanner";

// Eager — these three are the first surfaces every visitor sees and
// they share layout (AuthShell). Keeping them in the initial bundle
// avoids a Suspense flash on the highest-traffic paths.
import Landing from "./pages/Landing";
import Both from "./pages/Both";
import LoopLiveFeedDemo from "./pages/LoopLiveFeedDemo";
import Login from "./pages/Login";
import Signup from "./pages/Signup";
// Iter 212m-219 — Marketing "Why ORA" deep-dive. Lightweight page
// (< 6 KB gzipped) — no heavy deps, so we ship it in the initial
// bundle alongside Landing for zero-flash navigation from the hero.
import WhyOra from "./pages/WhyOra";
import { initTheme } from "./services/theme";

// Iter 212m-52 — apply theme as early as possible (BEFORE React
// renders) so the first paint already shows the right palette.
// Avoids the "dark flash before light mode resolves" foot-gun that
// every theme system hits if it waits for useEffect.
initTheme();

// Lazy — every other route is fetched on-demand. The browser cache
// memoises the chunks so the SECOND visit to /admin is instant.
const Dashboard         = lazy(() => import("./pages/Dashboard"));
const Deploy            = lazy(() => import("./pages/Deploy"));
const Domain            = lazy(() => import("./pages/Domain"));
const Settings          = lazy(() => import("./pages/Settings"));
const Tokens            = lazy(() => import("./pages/Tokens"));
const Analytics         = lazy(() => import("./pages/Analytics"));
const Projects          = lazy(() => import("./pages/Projects"));
const Integrations      = lazy(() => import("./pages/Integrations")); // Iter 212m-174
const Admin             = lazy(() => import("./pages/Admin"));
const AdminOverview     = lazy(() => import("./pages/AdminOverview"));
const AdminIntegrations = lazy(() => import("./pages/AdminIntegrations"));
const AdminFinancials   = lazy(() => import("./pages/AdminFinancials"));
const AdminVanguard     = lazy(() => import("./pages/AdminVanguard"));
const SystemStatsPage   = lazy(() => import("./pages/SystemStatsPage"));   // Iter 212m-153
const ToolsPage         = lazy(() => import("./pages/ToolsPage"));         // Iter 212m-158
const FeatureWindow     = lazy(() => import("./pages/FeatureWindow"));  // Iter 212m-64
const CodebaseHealth    = lazy(() => import("./pages/CodebaseHealth"));  // Iter 212m-72
const BugHunt           = lazy(() => import("./pages/BugHunt"));        // Iter 212m-75
const SidebarPreview    = lazy(() => import("./pages/SidebarPreview")); // Iter 212m-80
const DashboardPreviewV2 = lazy(() => import("./pages/DashboardPreviewV2")); // Iter 212m-81
const AdminApiKeys      = lazy(() => import("./pages/AdminApiKeys"));
const AdminSystemHealth = lazy(() => import("./pages/AdminSystemHealth"));   // Iter 212m-205
const PersonalTrackAdmin = lazy(() => import("./pages/admin/PersonalTrackAdmin")); // Iter 212m-240
const OraChat            = lazy(() => import("./pages/admin/OraChat"));            // Iter 212m-238
const OraDirect          = lazy(() => import("./pages/OraDirect"));                 // Iter 212m-241 public PIN-gated
const PolicyPage        = lazy(() => import("./pages/PolicyPage"));
const Wrapped           = lazy(() => import("./pages/Wrapped"));
const ShipWall          = lazy(() => import("./pages/ShipWall"));
const BrainDump         = lazy(() => import("./pages/BrainDump"));
const OpsRecipes        = lazy(() => import("./pages/OpsRecipes"));
const Automations       = lazy(() => import("./pages/Automations"));
const OAuthFinish       = lazy(() => import("./pages/OAuthFinish"));
const VsDevin           = lazy(() => import("./pages/VsDevin"));
const Pricing          = lazy(() => import("./pages/Pricing"));
const Demo             = lazy(() => import("./pages/Demo"));                  // Iter 212m-200
// Iter 212m-235 — Personal Track (Phase 6). Warm cream/terracotta
// aesthetic; distinct from Developer Track's IDE-dark shell.
const ChooseTrack      = lazy(() => import("./pages/personal/ChooseTrack"));
const BuildHome        = lazy(() => import("./pages/personal/BuildHome"));
const DraftReview      = lazy(() => import("./pages/personal/DraftReview"));
const ShipProgress     = lazy(() => import("./pages/personal/ShipProgress"));
const BuildSuccess     = lazy(() => import("./pages/personal/BuildSuccess"));

// Minimal loading state for the brief fetch window. Avoids the
// jarring "blank page" between click and hydration. Pure CSS so it
// renders before any React state.
function RouteLoader() {
  return (
    <div
      data-testid="route-loader"
      style={{
        position: "fixed",
        inset: 0,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "var(--bg, #0a0e1a)",
        color: "var(--text-faint, #6c7280)",
        fontSize: 12,
        letterSpacing: "0.08em",
        textTransform: "uppercase",
        fontFamily: "'JetBrains Mono', monospace",
      }}
    >
      loading…
    </div>
  );
}

// Iter 212m-25 — Tiny child of <BrowserRouter> that owns the
// auto-clear-console hook. Has to live INSIDE the router because
// the hook reads `useLocation()` to fire console.clear() on every
// route change. Rendering nothing visible.
function AutoClearConsoleHost() {
  useAutoClearConsole();
  return null;
}

// Meta Pixel — SPA route-change tracking. The base code in index.html
// fires PageView once on first load; this child of <BrowserRouter>
// fires it again on EVERY client-side navigation so all pages are
// tracked (skips the initial mount to avoid double-counting).
function MetaPixelRouteTracker() {
  const location = useLocation();
  const isFirst = useRef(true);
  useEffect(() => {
    if (isFirst.current) { isFirst.current = false; return; }
    if (typeof window.fbq === "function") {
      window.fbq("track", "PageView");
    }
  }, [location.pathname, location.search]);
  return null;
}

export default function App() {
  // Iter 101 — Capture `?ref=<uid>` on any landing, stash in localStorage
  // so Signup can attribute the referrer after account creation.
  // Also pings the public /referrals/track endpoint so the referrer
  // sees the click in their dashboard.
  useEffect(() => {
    try {
      const params = new URLSearchParams(window.location.search);
      const ref = params.get("ref");
      if (ref && ref.length > 0 && ref.length < 100) {
        localStorage.setItem("aurem_ref", ref);
        const backend = process.env.REACT_APP_BACKEND_URL;
        if (backend) {
          fetch(`${backend}/api/aurem-dev/referrals/track`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              ref_code: ref,
              path: window.location.pathname,
              user_agent: navigator.userAgent.slice(0, 200),
            }),
          }).catch(() => {});
        }
      }
    } catch { /* no-op */ }
  }, []);

  return (
    <BrowserRouter>
      <AutoClearConsoleHost />
      <MetaPixelRouteTracker />
      <Toaster />
      {/* Iter 212m-148 — Global FixJob provider owns the SSE so the
          job survives panel toggles, route changes, and backdrop
          clicks.  PersistentFixBar is the always-visible 44px chrome;
          FixProgressDrawer slides up from it. */}
      <FixJobProvider>
        <FixProgressDrawer />
        <PersistentFixBar />
      <Suspense fallback={<RouteLoader />}>
        <Routes>
          <Route path="/"                element={<Landing />} />
          <Route path="/both"            element={<Both />} />
          <Route path="/dev/loop-live-feed" element={<LoopLiveFeedDemo />} />
          <Route path="/why-ora"         element={<WhyOra />} />
          <Route path="/demo"            element={<Demo />} />
          <Route path="/login"           element={<Login />} />
          <Route path="/signup"          element={<Signup />} />
          <Route path="/dashboard"       element={<Dashboard />} />
          {/* Iter 212m-235 — Personal Track routes */}
          <Route path="/choose-track"    element={<ChooseTrack />} />
          <Route path="/ora"             element={<OraDirect />} />
          <Route path="/build"           element={<BuildHome />} />
          <Route path="/build/:draftId"  element={<DraftReview />} />
          <Route path="/build/:draftId/ship"    element={<ShipProgress />} />
          <Route path="/build/:draftId/success" element={<BuildSuccess />} />
          <Route path="/integrations"    element={<Integrations />} />
          <Route path="/deploy"          element={<Deploy />} />
          <Route path="/domain"          element={<Domain />} />
          <Route path="/settings"        element={<Settings />} />
          <Route path="/profile"         element={<Settings />} />
          <Route path="/tokens"          element={<Tokens />} />
          <Route path="/analytics"       element={<Analytics />} />
          <Route path="/projects"        element={<Projects />} />
          <Route path="/admin"           element={<Admin />} />
          <Route path="/admin/overview"  element={<AdminOverview />} />
          <Route path="/admin/integrations" element={<AdminIntegrations />} />
          <Route path="/admin/financials"   element={<AdminFinancials />} />
          <Route path="/admin/vanguard"     element={<AdminVanguard />} />
          <Route path="/admin/system-stats" element={<SystemStatsPage />} />
          <Route path="/admin/observability" element={<SystemStatsPage />} />
          <Route path="/tools" element={<ToolsPage />} />
          {/* Iter 212m-198 — Sidebar Bug Hunt bug: `/tools/bug-hunt` was
              rendering the marketing landing (BugHunt.jsx), so clicking
              Bug Hunt from the sidebar looked like it took the user to
              the homepage instead of the scanner. The public marketing
              URL `/bug-hunt` (below) still points at BugHunt.jsx so
              SEO + conversion funnel are preserved. Sidebar entry now
              lands on the CodebaseHealth scanner where the "Bug Hunt"
              category tile is one click away. */}
          <Route path="/tools/bug-hunt"    element={<CodebaseHealth />} />
          <Route path="/tools/health-scan" element={<CodebaseHealth />} />
          <Route path="/feature-window"     element={<FeatureWindow />} />
          <Route path="/admin/system-map"   element={<FeatureWindow />} />
          <Route path="/codebase-health"    element={<CodebaseHealth />} />
          <Route path="/health"             element={<CodebaseHealth />} />
          <Route path="/bug-hunt"           element={<BugHunt />} />
          <Route path="/sidebar-preview"    element={<SidebarPreview />} />
          <Route path="/dashboard-preview-v2" element={<DashboardPreviewV2 />} />
          <Route path="/admin/api-keys"     element={<AdminApiKeys />} />
          <Route path="/admin/system-health" element={<AdminSystemHealth />} />
          <Route path="/admin/personal-track" element={<PersonalTrackAdmin />} />
          <Route path="/admin/ora-chat"       element={<OraChat />} />
          {/* Iter 212m-171 — direct URLs for new admin sections */}
          <Route path="/admin/bin-tracker"    element={<Admin initialTab="bin_tracker" />} />
          <Route path="/admin/feature-flags"  element={<Admin initialTab="feature_flags" />} />
          <Route path="/admin/llm-credits"    element={<Admin initialTab="llm_credits" />} />
          <Route path="/admin/parliament-live" element={<Admin initialTab="parliament_live" />} />
          <Route path="/privacy"            element={<PolicyPage slug="privacy" />} />
          <Route path="/terms"              element={<PolicyPage slug="terms" />} />
          <Route path="/acceptable-use"     element={<PolicyPage slug="acceptable-use" />} />
          <Route path="/cookie-policy"      element={<PolicyPage slug="cookie-policy" />} />
          <Route path="/cookie-preferences" element={<PolicyPage slug="cookie-policy" />} />
          <Route path="/refund-policy"      element={<PolicyPage slug="refund-policy" />} />
          <Route path="/ai-code-processing" element={<PolicyPage slug="ai-code-processing" />} />
          <Route path="/subprocessors"      element={<PolicyPage slug="subprocessors" />} />
          <Route path="/dpa"                element={<PolicyPage slug="dpa" />} />
          <Route path="/security"           element={<PolicyPage slug="security" />} />
          <Route path="/status"             element={<PolicyPage slug="status" />} />
          <Route path="/admin/architecture" element={<Admin initialTab="arch" />} />
          <Route path="/admin/ops" element={<OpsRecipes />} />
          <Route path="/admin/brain/:projectId" element={<BrainDump />} />
          <Route path="/wall" element={<ShipWall />} />
          <Route path="/wrapped" element={<Wrapped />} />
          <Route path="/automations" element={<Automations />} />
          <Route path="/oauth-finish" element={<OAuthFinish />} />
          <Route path="/vs/devin"  element={<VsDevin />} />
          <Route path="/pricing" element={<Pricing />} />
          {/* Iter 124h — /vs/cursor was a dead link in Landing footer pre-fix;
              keep the URL alive (incoming backlinks) by redirecting to /vs/devin
              until a dedicated VsCursor page lands. */}
          <Route path="/vs/cursor" element={<Navigate to="/vs/devin" replace />} />
          {/* Iter 212m-57 — Any /dashboard/<anything> subroute (e.g. the
              "/dashboard/new" URL some new-project deep-links generated)
              must redirect to /dashboard rather than getting swept up by
              the catch-all below — which sent users to "/" and read as
              "session was killed". The auth token lives in localStorage,
              not the URL, so this redirect preserves the session. */}
          <Route path="/dashboard/*" element={<Navigate to="/dashboard" replace />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
      </FixJobProvider>
      <CookieConsentBanner />
    </BrowserRouter>
  );
}
