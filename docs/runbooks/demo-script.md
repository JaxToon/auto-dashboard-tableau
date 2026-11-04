# Stakeholder demo script (5–7 minutes)

Before the demo: start API and web (README). For a real demo, configure Tableau (`TABLEAU_MODE=rest`). If Tableau is not yet available, say so explicitly and use mock mode, which the UI labels "Mock publish (not real)".

1. **Open the app** and point at the hatched `POC / SYNTHETIC DATA` band. Sign in as *Demo Business User* (labelled mock login).
2. **Ask:** "Show monthly SME credit outstanding by region for 2025."
3. **Interpretation:** show dataset, period, breakdown and the KPI definition used (Total Credit Outstanding, period-end balance). Note the recommended output: Dashboard, with the reason.
4. **Clarification (optional):** ask "Tampilkan kinerja kredit per region" and choose a KPI. Then ask "Show NPL ratio by region" to show a safe refusal (no invented KPI).
5. **Build dashboard draft**, watch the steps (Understanding → Checking definitions → Building → Validating), select *Review dashboard*.
6. **Preview:** KPI cards (Rp 11.95 T as of Dec 2025), monthly trend by region, regional comparison.
7. **Provenance:** open *KPI definitions and data source*: formula, calculation notes, catalog version, synthetic classification, data freshness.
8. **Review gate:** *Submit for review* → switch user to *Demo BA Reviewer* → approve with a comment → switch back.
9. **Publish to Tableau** → confirm. Show the verification checks.
10. **Open in Tableau** → sign in as the Tableau test user.
11. **Compare** KPI and regional values with the preview.
12. **Optional:** as reviewer, open *Recommendations* → *Find recurring questions* (simulated events, labelled) → *Why recommended* → *Create dashboard draft*.

Close by distinguishing: real vs mock integration, synthetic vs internal data, implemented vs future (conversational analytics adapter, SSO, Hyper extract fallback).
