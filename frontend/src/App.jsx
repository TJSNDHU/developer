import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { useEffect } from "react";
import Toaster from "./components/Toast";
import Landing from "./pages/Landing";
import Login from "./pages/Login";
import Signup from "./pages/Signup";
import Dashboard from "./pages/Dashboard";
import Deploy from "./pages/Deploy";
import Database from "./pages/Database";
import Domain from "./pages/Domain";
import Settings from "./pages/Settings";
import Tokens from "./pages/Tokens";
import Analytics from "./pages/Analytics";
import Projects from "./pages/Projects";
import Admin from "./pages/Admin";
import AdminOverview from "./pages/AdminOverview";
import AdminIntegrations from "./pages/AdminIntegrations";
import AdminFinancials from "./pages/AdminFinancials";
import PolicyPage from "./pages/PolicyPage";
import Wrapped from "./pages/Wrapped";
import ShipWall from "./pages/ShipWall";
import BrainDump from "./pages/BrainDump";
import OpsRecipes from "./pages/OpsRecipes";
import Automations from "./pages/Automations";
import OAuthFinish from "./pages/OAuthFinish";

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
      <Toaster />
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/login" element={<Login />} />
        <Route path="/signup" element={<Signup />} />
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/deploy" element={<Deploy />} />
        <Route path="/database" element={<Database />} />
        <Route path="/domain" element={<Domain />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/tokens" element={<Tokens />} />
        <Route path="/analytics" element={<Analytics />} />
        <Route path="/projects" element={<Projects />} />
        <Route path="/admin" element={<Admin />} />
        <Route path="/admin/overview" element={<AdminOverview />} />
        <Route path="/admin/integrations" element={<AdminIntegrations />} />
        <Route path="/admin/financials"   element={<AdminFinancials />} />
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
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
