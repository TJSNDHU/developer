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
import ShipWall from "./pages/ShipWall";

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
        <Route path="/wall" element={<ShipWall />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
