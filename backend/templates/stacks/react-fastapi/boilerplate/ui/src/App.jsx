import React, { useEffect, useState } from "react";
import { Toaster, toast } from "sonner";
import { LogIn, LogOut, Plus, Trash2 } from "lucide-react";

const API = import.meta.env.VITE_API_URL || "http://localhost:8001";

export default function App() {
  const [token, setToken] = useState(localStorage.getItem("token") || "");
  const [items, setItems] = useState([]);
  const [title, setTitle] = useState("");

  const auth = { Authorization: `Bearer ${token}` };

  async function loadItems() {
    if (!token) return;
    const r = await fetch(`${API}/api/items`, { headers: auth });
    if (r.ok) setItems((await r.json()).items || []);
  }

  useEffect(() => { loadItems(); }, [token]);

  async function login(e) {
    e.preventDefault();
    const fd = new FormData(e.target);
    const r = await fetch(`${API}/api/auth/login`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: fd.get("email"), password: fd.get("password") }),
    });
    if (!r.ok) return toast.error("Invalid credentials");
    const { token } = await r.json();
    localStorage.setItem("token", token);
    setToken(token);
    toast.success("Signed in");
  }

  async function addItem(e) {
    e.preventDefault();
    if (!title.trim()) return;
    const r = await fetch(`${API}/api/items`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...auth },
      body: JSON.stringify({ title }),
    });
    if (r.ok) { setTitle(""); loadItems(); toast.success("Added"); }
  }

  async function del(id) {
    const r = await fetch(`${API}/api/items/${id}`, { method: "DELETE", headers: auth });
    if (r.ok) { loadItems(); toast.success("Deleted"); }
  }

  function logout() {
    localStorage.removeItem("token");
    setToken("");
    setItems([]);
  }

  if (!token) {
    return (
      <div style={{ maxWidth: 360, margin: "10vh auto", fontFamily: "system-ui" }}>
        <Toaster />
        <h1>Welcome</h1>
        <form onSubmit={login} style={{ display: "grid", gap: 12 }}>
          <input name="email" type="email" placeholder="Email" required
                 style={{ padding: 10, borderRadius: 8, border: "1px solid #ccc" }} />
          <input name="password" type="password" placeholder="Password" required minLength={8}
                 style={{ padding: 10, borderRadius: 8, border: "1px solid #ccc" }} />
          <button type="submit"
                  style={{ padding: 12, borderRadius: 8, background: "#0ea5e9", color: "#fff", border: 0, cursor: "pointer" }}>
            <LogIn size={16} style={{ verticalAlign: "middle", marginRight: 6 }} />
            Sign in
          </button>
        </form>
        <p style={{ fontSize: 13, opacity: 0.7, marginTop: 16 }}>
          POST /api/auth/signup with the same body to create a new account.
        </p>
      </div>
    );
  }

  return (
    <div style={{ maxWidth: 640, margin: "5vh auto", fontFamily: "system-ui", padding: 16 }}>
      <Toaster />
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h1>Your Items</h1>
        <button onClick={logout} style={{ background: "transparent", border: "1px solid #ccc",
                borderRadius: 8, padding: "6px 12px", cursor: "pointer" }}>
          <LogOut size={14} style={{ verticalAlign: "middle", marginRight: 4 }} />
          Sign out
        </button>
      </header>

      <form onSubmit={addItem} style={{ display: "flex", gap: 8, marginTop: 20 }}>
        <input value={title} onChange={(e) => setTitle(e.target.value)}
               placeholder="What do you want to remember?"
               style={{ flex: 1, padding: 10, borderRadius: 8, border: "1px solid #ccc" }} />
        <button type="submit"
                style={{ padding: "0 16px", borderRadius: 8, background: "#0ea5e9", color: "#fff", border: 0, cursor: "pointer" }}>
          <Plus size={16} />
        </button>
      </form>

      <ul style={{ listStyle: "none", padding: 0, marginTop: 20 }}>
        {items.map((it) => (
          <li key={it.id} style={{ display: "flex", justifyContent: "space-between",
                                    padding: "10px 12px", borderRadius: 8, background: "#f6f7f9",
                                    marginBottom: 8 }}>
            <span>{it.title}</span>
            <button onClick={() => del(it.id)}
                    style={{ background: "transparent", border: 0, cursor: "pointer", color: "#dc2626" }}>
              <Trash2 size={16} />
            </button>
          </li>
        ))}
        {items.length === 0 && <p style={{ opacity: 0.6 }}>No items yet.</p>}
      </ul>
    </div>
  );
}
