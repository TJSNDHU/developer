import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
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
import Wrapped from "./pages/Wrapped";
import ShipWall from "./pages/ShipWall";
import BrainDump from "./pages/BrainDump";
import OpsRecipes from "./pages/OpsRecipes";
import Automations from "./pages/Automations";

export default function App() {
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
        <Route path="/admin/architecture" element={<Admin initialTab="arch" />} />
        <Route path="/admin/ops" element={<OpsRecipes />} />
        <Route path="/admin/brain/:projectId" element={<BrainDump />} />
        <Route path="/wall" element={<ShipWall />} />
        <Route path="/wrapped" element={<Wrapped />} />
        <Route path="/automations" element={<Automations />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
