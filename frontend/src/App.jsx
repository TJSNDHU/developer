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
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import useAutoClearConsole from "./lib/useAutoClearConsole";
import { useEffect, lazy, Suspense } from "react";
import Toaster from "./components/Toast";

// Eager — these three are the first surfaces every visitor sees and
// they share layout (AuthShell). Keeping them in the initial bundle
// avoids a Suspense flash on the highest-traffic paths.
import Landing from "./pages/Landing";
import Login from "./pages/Login";
import Signup from "./pages/Signup";
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
const Database          = lazy(() => import("./pages/Database"));
const Domain            = lazy(() => import("./pages/Domain"));
const Settings          = lazy(() => import("./pages/Settings"));
const Tokens            = lazy(() => import("./pages/Tokens"));
const Analytics         = lazy(() => import("./pages/Analytics"));
const Projects          = lazy(() => import("./pages/Projects"));
const Admin             = lazy(() => import("./pages/Admin"));
const AdminOverview     = lazy(() => import("./pages/AdminOverview"));
const AdminIntegrations = lazy(() => import("./pages/AdminIntegrations"));
const AdminFinancials   = lazy(() => import("./pages/AdminFinancials"));
const AdminVanguard     = lazy(() => import("./pages/AdminVanguard"));
const FeatureWindow     = lazy(() => import("./pages/FeatureWindow"));  // Iter 212m-64
const AdminApiKeys      = lazy(() => import("./pages/AdminApiKeys"));
const PolicyPage        = lazy(() => import("./pages/PolicyPage"));
const Wrapped           = lazy(() => import("./pages/Wrapped"));
const ShipWall          = lazy(() => import("./pages/ShipWall"));
const BrainDump         = lazy(() => import("./pages/BrainDump"));
const OpsRecipes        = lazy(() => import("./pages/OpsRecipes"));
const Automations       = lazy(() => import("./pages/Automations"));
const OAuthFinish       = lazy(() => import("./pages/OAuthFinish"));
const VsDevin           = lazy(() => import("./pages/VsDevin"));
const Pricing           = lazy(() => import("./pages/Pricing"));

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
      <Toaster />
      <Suspense fallback={<RouteLoader />}>
        <Routes>
          <Route path="/"                element={<Landing />} />
          <Route path="/login"           element={<Login />} />
          <Route path="/signup"          element={<Signup />} />
          <Route path="/dashboard"       element={<Dashboard />} />
          <Route path="/deploy"          element={<Deploy />} />
          <Route path="/database"        element={<Database />} />
          <Route path="/domain"          element={<Domain />} />
          <Route path="/settings"        element={<Settings />} />
          <Route path="/tokens"          element={<Tokens />} />
          <Route path="/analytics"       element={<Analytics />} />
          <Route path="/projects"        element={<Projects />} />
          <Route path="/admin"           element={<Admin />} />
          <Route path="/admin/overview"  element={<AdminOverview />} />
          <Route path="/admin/integrations" element={<AdminIntegrations />} />
          <Route path="/admin/financials"   element={<AdminFinancials />} />
          <Route path="/admin/vanguard"     element={<AdminVanguard />} />
          <Route path="/feature-window"     element={<FeatureWindow />} />
          <Route path="/admin/system-map"   element={<FeatureWindow />} />
          <Route path="/admin/api-keys"     element={<AdminApiKeys />} />
          <Route path="/privacy"        element={<PolicyPage slug="privacy" />} />
          <Route path="/terms"          element={<PolicyPage slug="terms" />} />
          <Route path="/acceptable-use" element={<PolicyPage slug="acceptable-use" />} />
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
    </BrowserRouter>
  );
}
