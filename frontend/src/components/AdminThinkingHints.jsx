/**
 * AdminThinkingHints.jsx — Iter 158
 *
 * Admin CRUD UI for the tier-aware "thinking hints" shown next to the
 * chat spinner. Mounted inside the Admin → Settings tab so the founder
 * can iterate copy without a redeploy.
 *
 * Behaviour
 * ─────────
 *   - Lists every hint grouped by tier (free → starter → pro → team → founder)
 *   - "Add new" button slides open a blank inline editor
 *   - Each row is editable in-place; Save / Cancel inline
 *   - Active toggle is a one-click switch
 *   - Delete asks for confirmation
 *
 * All mutations go through:
 *   POST   /api/aurem-dev/admin/thinking-hints
 *   PUT    /api/aurem-dev/admin/thinking-hints/{hint_id}
 *   DELETE /api/aurem-dev/admin/thinking-hints/{hint_id}
 *
 * The chat-side cache is busted server-side so edits are visible
 * within 60 seconds in the wild (or immediately for the next pick).
 */
import { useCallback, useEffect, useState } from "react";
import { Plus, Save, X, Trash2, Edit3, ToggleLeft, ToggleRight } from "lucide-react";
import { api } from "../lib/api";
import { toast } from "./Toast";

const TIERS = ["free", "starter", "pro", "team", "founder"];

const TIER_COLOUR = {
  free:     "#a39d8a",
  starter:  "#6ee7b7",
  pro:      "#ffc560",
  team:     "#ff8a2a",
  founder:  "#c084fc",
};

function blank(tier = "free") {
  return {
    hint_id: "",
    tier,
    emoji: "💡",
    headline: "",
    body: "",
    cta_text: "",
    cta_link: "",
    active: true,
    weight: 10,
  };
}

export default function AdminThinkingHints() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(null); // hint_id or "__new__"
  const [draft, setDraft] = useState(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.get("/admin/thinking-hints");
      setItems(r.data?.items || []);
    } catch (e) {
      toast({
        message: e?.response?.data?.detail || "Failed to load hints",
        kind: "error",
      });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  function startNew() {
    setDraft(blank("free"));
    setEditing("__new__");
  }

  function startEdit(h) {
    setDraft({ ...h });
    setEditing(h.hint_id);
  }

  function cancelEdit() {
    setEditing(null);
    setDraft(null);
  }

  async function saveDraft() {
    if (!draft) return;
    if (!draft.headline.trim() || !draft.body.trim()) {
      toast({ message: "Headline and body are required", kind: "error" });
      return;
    }
    setSaving(true);
    try {
      if (editing === "__new__") {
        await api.post("/admin/thinking-hints", draft);
        toast({ message: "Hint created", kind: "success" });
      } else {
        await api.put(`/admin/thinking-hints/${editing}`, draft);
        toast({ message: "Hint updated", kind: "success" });
      }
      await load();
      cancelEdit();
    } catch (e) {
      toast({
        message: e?.response?.data?.detail || "Save failed",
        kind: "error",
      });
    } finally {
      setSaving(false);
    }
  }

  async function toggleActive(h) {
    try {
      await api.put(`/admin/thinking-hints/${h.hint_id}`, {
        ...h, active: !h.active,
      });
      await load();
    } catch (e) {
      toast({ message: "Toggle failed", kind: "error" });
    }
  }

  async function remove(h) {
    if (!window.confirm(`Delete hint "${h.headline}"?`)) return;
    try {
      await api.delete(`/admin/thinking-hints/${h.hint_id}`);
      await load();
      toast({ message: "Hint deleted", kind: "success" });
    } catch (e) {
      toast({ message: "Delete failed", kind: "error" });
    }
  }

  const grouped = TIERS.reduce((acc, t) => {
    acc[t] = items.filter((x) => x.tier === t);
    return acc;
  }, {});

  return (
    <div data-testid="admin-thinking-hints" style={{ marginTop: 28 }}>
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        marginBottom: 12,
      }}>
        <div>
          <h3 style={{ fontSize: 13, margin: "0 0 4px" }}>
            💡 Thinking Hints
            <span style={{
              marginLeft: 10, fontSize: 10, fontWeight: 400,
              padding: "2px 8px", borderRadius: 999,
              background: "rgba(255,138,42,0.10)",
              border: "1px solid rgba(255,138,42,0.30)",
              color: "var(--accent-2, #ffc560)",
              letterSpacing: "0.1em",
            }}>
              {items.length} TOTAL · {items.filter((x) => x.active).length} ACTIVE
            </span>
          </h3>
          <p style={{ fontSize: 11, color: "var(--text-faint)", margin: 0 }}>
            Tier-aware upsell cards shown beside the chat "thinking…" spinner.
            Edits go live within 60 seconds.
          </p>
        </div>
        <button
          data-testid="thinking-hint-new"
          onClick={startNew}
          className="btn-primary"
          style={{
            fontSize: 11, padding: "6px 12px",
            display: "inline-flex", alignItems: "center", gap: 6,
          }}
        >
          <Plus size={13} /> Add hint
        </button>
      </div>

      {editing === "__new__" && (
        <HintEditor
          draft={draft}
          setDraft={setDraft}
          onSave={saveDraft}
          onCancel={cancelEdit}
          saving={saving}
          isNew
        />
      )}

      {loading ? (
        <div style={{ padding: 24, color: "var(--text-faint)", fontSize: 12 }}>
          Loading hints…
        </div>
      ) : (
        TIERS.map((tier) => (
          <div key={tier} style={{ marginBottom: 18 }}>
            <div style={{
              display: "flex", alignItems: "center", gap: 8,
              padding: "8px 0", borderBottom: "1px solid var(--border)",
              marginBottom: 8,
            }}>
              <span style={{
                width: 8, height: 8, borderRadius: "50%",
                background: TIER_COLOUR[tier],
                boxShadow: `0 0 8px ${TIER_COLOUR[tier]}66`,
              }} />
              <span style={{
                textTransform: "uppercase", fontSize: 10,
                letterSpacing: "0.18em", fontWeight: 600,
                color: TIER_COLOUR[tier],
              }}>
                {tier}
              </span>
              <span style={{ fontSize: 11, color: "var(--text-faint)" }}>
                ({grouped[tier].length})
              </span>
            </div>
            {grouped[tier].length === 0 ? (
              <div style={{ padding: "8px 12px", fontSize: 11,
                            color: "var(--text-faint)", fontStyle: "italic" }}>
                No hints for this tier yet.
              </div>
            ) : (
              grouped[tier].map((h) => (
                editing === h.hint_id ? (
                  <HintEditor
                    key={h.hint_id}
                    draft={draft}
                    setDraft={setDraft}
                    onSave={saveDraft}
                    onCancel={cancelEdit}
                    saving={saving}
                  />
                ) : (
                  <HintRow
                    key={h.hint_id}
                    hint={h}
                    onEdit={() => startEdit(h)}
                    onToggle={() => toggleActive(h)}
                    onDelete={() => remove(h)}
                  />
                )
              ))
            )}
          </div>
        ))
      )}
    </div>
  );
}

function HintRow({ hint, onEdit, onToggle, onDelete }) {
  return (
    <div
      data-testid={`hint-row-${hint.hint_id}`}
      style={{
        display: "grid",
        gridTemplateColumns: "32px 1fr auto auto auto auto",
        gap: 10, alignItems: "center",
        padding: "10px 12px",
        borderRadius: 8,
        background: hint.active
          ? "rgba(255,255,255,0.02)"
          : "rgba(255,255,255,0.01)",
        border: "1px solid var(--border)",
        marginBottom: 6,
        opacity: hint.active ? 1 : 0.5,
        transition: "opacity 160ms",
      }}
    >
      <span style={{ fontSize: 18, textAlign: "center" }}>{hint.emoji || "·"}</span>
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text)" }}>
          {hint.headline}
        </div>
        <div style={{ fontSize: 11, color: "var(--text-dim)",
                      overflow: "hidden", textOverflow: "ellipsis",
                      whiteSpace: "nowrap" }}>
          {hint.body}
        </div>
      </div>
      <span style={{
        fontSize: 10, fontFamily: "'JetBrains Mono', monospace",
        color: "var(--text-faint)", padding: "2px 6px",
        background: "rgba(255,255,255,0.04)", borderRadius: 4,
      }}>
        w:{hint.weight}
      </span>
      <button
        data-testid={`hint-toggle-${hint.hint_id}`}
        onClick={onToggle}
        title={hint.active ? "Deactivate" : "Activate"}
        style={{
          background: "none", border: "none", cursor: "pointer",
          color: hint.active ? "var(--ok, #6ee7b7)" : "var(--text-faint)",
          padding: 4,
        }}
      >
        {hint.active ? <ToggleRight size={20} /> : <ToggleLeft size={20} />}
      </button>
      <button
        data-testid={`hint-edit-${hint.hint_id}`}
        onClick={onEdit}
        title="Edit"
        style={{
          background: "none", border: "none", cursor: "pointer",
          color: "var(--text-dim)", padding: 4,
        }}
      >
        <Edit3 size={14} />
      </button>
      <button
        data-testid={`hint-delete-${hint.hint_id}`}
        onClick={onDelete}
        title="Delete"
        style={{
          background: "none", border: "none", cursor: "pointer",
          color: "var(--danger, #ef4444)", padding: 4,
        }}
      >
        <Trash2 size={14} />
      </button>
    </div>
  );
}

function HintEditor({ draft, setDraft, onSave, onCancel, saving, isNew }) {
  if (!draft) return null;
  const set = (k, v) => setDraft({ ...draft, [k]: v });
  return (
    <div
      data-testid="hint-editor"
      style={{
        padding: 14, marginBottom: 10,
        borderRadius: 10,
        background: "rgba(255,138,42,0.04)",
        border: "1px solid rgba(255,138,42,0.30)",
        display: "grid", gap: 10,
      }}
    >
      <div style={{ display: "grid", gridTemplateColumns: "80px 1fr 1fr 80px", gap: 10 }}>
        <div>
          <Label>Emoji</Label>
          <input
            data-testid="hint-field-emoji"
            className="input"
            style={{ textAlign: "center", fontSize: 16 }}
            maxLength={4}
            value={draft.emoji || ""}
            onChange={(e) => set("emoji", e.target.value)}
          />
        </div>
        <div>
          <Label>Tier</Label>
          <select
            data-testid="hint-field-tier"
            className="input"
            value={draft.tier}
            onChange={(e) => set("tier", e.target.value)}
          >
            {TIERS.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>
        <div>
          <Label>CTA link</Label>
          <input
            data-testid="hint-field-cta-link"
            className="input"
            placeholder="/settings#billing"
            value={draft.cta_link || ""}
            onChange={(e) => set("cta_link", e.target.value)}
          />
        </div>
        <div>
          <Label>Weight</Label>
          <input
            data-testid="hint-field-weight"
            className="input"
            type="number" min={1} max={100}
            value={draft.weight}
            onChange={(e) => set("weight", +e.target.value || 10)}
          />
        </div>
      </div>
      <div>
        <Label>Headline · max 80 chars</Label>
        <input
          data-testid="hint-field-headline"
          className="input"
          maxLength={80}
          placeholder="Loving AUREM? Unlock more."
          value={draft.headline}
          onChange={(e) => set("headline", e.target.value)}
        />
      </div>
      <div>
        <Label>Body · max 180 chars</Label>
        <textarea
          data-testid="hint-field-body"
          className="input"
          maxLength={180}
          rows={2}
          placeholder="Starter $9/mo — 50 tasks + Project Brain memory."
          value={draft.body}
          onChange={(e) => set("body", e.target.value)}
        />
      </div>
      <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
        <Label inline>CTA text</Label>
        <input
          data-testid="hint-field-cta-text"
          className="input"
          style={{ flex: 1 }}
          maxLength={32}
          placeholder="Upgrade in 30s"
          value={draft.cta_text || ""}
          onChange={(e) => set("cta_text", e.target.value)}
        />
        <label style={{
          display: "inline-flex", alignItems: "center", gap: 6,
          fontSize: 11, color: "var(--text-dim)",
          cursor: "pointer",
        }}>
          <input
            data-testid="hint-field-active"
            type="checkbox"
            checked={!!draft.active}
            onChange={(e) => set("active", e.target.checked)}
          />
          Active
        </label>
      </div>
      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
        <button
          data-testid="hint-editor-cancel"
          onClick={onCancel} className="btn-ghost"
          style={{ fontSize: 11, gap: 6 }}
        >
          <X size={13} /> Cancel
        </button>
        <button
          data-testid="hint-editor-save"
          onClick={onSave} disabled={saving} className="btn-primary"
          style={{ fontSize: 11, gap: 6 }}
        >
          <Save size={13} /> {saving ? "Saving…" : (isNew ? "Create" : "Save")}
        </button>
      </div>
    </div>
  );
}

function Label({ children, inline }) {
  return (
    <div style={{
      fontSize: 10, letterSpacing: "0.14em", textTransform: "uppercase",
      color: "var(--text-faint)", marginBottom: inline ? 0 : 4,
      whiteSpace: inline ? "nowrap" : "normal",
    }}>
      {children}
    </div>
  );
}
