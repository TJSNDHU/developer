module.exports = {
  content: ["./index.html", "./src/**/*.{js,jsx,ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Legacy tokens — DO NOT REMOVE (used across existing pages)
        "cto-bg":       "#0a0e1a",
        "cto-panel":    "#0f1525",
        "cto-border":   "#1e2638",
        "cto-text":     "#f1f5f9",
        "cto-muted":    "#94a3b8",
        "cto-accent":   "#f59e0b",
        "cto-accent-2": "#fbbf24",
        // Iter 212m-81 — Dashboard v2 (v0 design) semantic tokens.
        // Resolve from CSS vars scoped to `.ds2-root` so we never
        // pollute existing pages.
        background:           "var(--ds2-bg)",
        foreground:           "var(--ds2-fg)",
        border:               "var(--ds2-border)",
        primary:              "var(--ds2-primary)",
        "primary-foreground": "var(--ds2-primary-fg)",
        secondary:            "var(--ds2-secondary)",
        "secondary-foreground": "var(--ds2-secondary-fg)",
        muted:                "var(--ds2-muted)",
        "muted-foreground":   "var(--ds2-muted-fg)",
        card:                 "var(--ds2-card)",
        "card-foreground":    "var(--ds2-card-fg)",
        popover:              "var(--ds2-popover)",
        "popover-foreground": "var(--ds2-popover-fg)",
        sidebar:              "var(--ds2-sidebar)",
        "sidebar-foreground": "var(--ds2-sidebar-fg)",
        "sidebar-border":     "var(--ds2-sidebar-border)",
        "sidebar-accent":     "var(--ds2-sidebar-accent)",
        success:              "var(--ds2-success)",
        warning:              "var(--ds2-warning)",
        destructive:          "var(--ds2-destructive)",
        "chart-4":            "var(--ds2-chart-4)",
      },
      animation: {
        "loop-spin": "spin 1.6s linear infinite",
      },
    },
  },
  plugins: [],
};
