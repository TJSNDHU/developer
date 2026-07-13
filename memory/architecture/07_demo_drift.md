# 07 · Demo drift prevention

**Owner:** whoever ships user-facing UI changes.
**Created:** Iter 212m-200 (Feb 13, 2026)

The public `/demo` route + landing embed + interactive dashboard
tour are CSS-animated fabrications, not screen recordings.  They are
faithful mocks of the real product, and they lie next to real code
in the repo.  This is deliberate — it lets us update the demo in the
same PR as the UI change instead of re-recording a video.

## Reality contract (what the demo claims exists)

The animated walkthrough currently promises new visitors that these
features exist and work end-to-end.  If any of these ships broken,
the demo lies:

| Feature                     | Where in demo         | Live surface                                   |
|-----------------------------|-----------------------|------------------------------------------------|
| Signup w/ 10 free tasks     | Step 1 (`StepSignup`) | `/signup` page + auth-shell                   |
| Empty-dashboard CTA         | Step 2                | `ConnectRepoBanner` + `FinishSetupBanner`     |
| PAT + owner/repo connect    | Step 3                | `AddProjectWizard`                            |
| Sidebar green-dot indicator | Step 4                | `SidebarBound` — status polling                |
| `/scan bug hunt` slash cmd  | Step 5                | `SlashCommandMenu` + `chat.py` handler         |
| Ask Advisor (Council live)  | Step 5 side panel     | `AskAdvisorReal` + `/admin/council/health`     |
| LOOP mode phase bar         | Step 6                | `LoopStepBar` + loop orchestrator              |
| PR shipped / Vanguard OK    | Step 7                | `ShipConfirmModal` + Vanguard review           |

## Backlog item — "UI change hone par demo bhi update karna"

**Whenever you ship any user-visible change to a surface in the table
above, you MUST audit the demo in the same PR:**

1. `/app/frontend/src/components/demo/demoSteps.jsx` — mock UI shapes
2. `/app/frontend/src/components/demo/WalkthroughPlayer.jsx` — frame
3. `/app/frontend/src/pages/Demo.jsx` — captions / total duration
4. `/app/frontend/src/components/tour/ConnectRepoTour.jsx` — selectors
   used to spotlight real DOM elements (if you rename a `data-testid`,
   update the tour's `STEPS[*].selectors`).

Pre-merge checklist:
- [ ] Open `/demo` locally, watch the full loop, confirm captions still
      describe what's on screen.
- [ ] Open `/demo?mode=teaser` — same.
- [ ] On `/dashboard?tour=connect-repo`, run through all 3 tour steps
      and confirm each spotlight lands on a visible element.

## Selector contract (interactive tour)

The dashboard tour uses these `data-testid` anchors.  Do not rename
them without also updating `ConnectRepoTour.STEPS[*].selectors`:

- `ds2-add-repo`, `ds2-sidebar-repos`, `add-project-button`
- `chat-github-status`, `chat-form`
- `loop-step-bar`, `loop-mode-toggle`

If a target element genuinely goes away, add a graceful fallback
rectangle to that step's `fallback` field.

## Production checklist (before making /demo a marketing CTA)

Before pointing paid traffic at `/demo`, the following must be verified
LIVE on `auremcto.com` (not preview):

- [ ] `Ask Advisor` side panel loads on signed-in dashboard
- [ ] `chat-github-status` green dot lights up on a connected repo
- [ ] Council A health probe returns `live: true`
- [ ] `/scan` slash command works end-to-end
- [ ] Suggestion box submits (`/api/aurem-dev/suggestions`) — 200 OK

Rationale: the demo is fiction, but every scene it fabricates must be
achievable in one click on the real product.  If the real product
regresses, the demo becomes false advertising.
