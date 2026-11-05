# Tableau dashboard design system

The generated workbook (`apps/api/app/tableau_artifact.py`) is laid out by a small composer that
reads the shape of the validated content and picks, from fixed and tested building blocks, the
chart form for every KPI, the arrangement and the theme (`apps/api/app/tableau_theme.py`).

The rules were distilled from well-regarded business dashboard templates (Tableau Accelerators
and business-themed Viz of the Day dashboards), by parsing their workbook XML and looking at their
thumbnails. No template content is copied or shipped. Reference workbooks are kept outside the
repository.

## Page structure: summary, then detail

1. **Header**: dataset label, title, period, and the "POC / SYNTHETIC DATA" notice.
2. **Filters**: one compact control per filterable dimension.
3. **KPI cards (BANs)**: short label, big value, change vs the previous period (▲/▼, coloured by
   whether the change is good, using the catalog's `higher_is_better`), and a sparkline. A growth
   rate shows the period it covers instead of a change.
4. **Hero chart**: the trend (or the first chart), next to a **What stands out** panel with the
   deterministic findings.
5. **Detail**: every other chart in a card grid below the summary. Cards grow with the number of
   members, so labels never overlap.

## Chart forms

The planner still produces the validated `line` / `bar` / `table` visualizations. The composer
only changes the visual encoding, never the data or the KPI formula:

| Content | Form |
|---|---|
| Trend without breakdown | Area chart with the latest value labelled |
| Trend by up to 6 members | Spotlight lines: the leader in the accent, the runner-up in the highlight, the rest grey; each line labelled at its end |
| Trend by more members | Heatmap (member × period, darker = higher), sorted by value |
| Achievement vs target (ratio KPI) | Bars against a 100% target line; members below target in the highlight colour |
| Growth rate by member | Dots against an average line |
| Amount by member | Ranked bars, the top 3 in the accent (a table calculation, so it follows the filters) |
| Amount over time | Columns, the latest period in the accent |

Each chart has a short headline computed from the validated rows ("Jakarta leads with Rp 2.15 T",
"6 of 8 below the 100% target") and a subtitle with the KPI and the period.

Every worksheet is clean: no gridlines, zero lines, row dividers or axis ticks; values labelled on
the marks; Tableau's own fonts.

## Arrangements and themes

| Arrangement | When "auto" can pick it |
|---|---|
| `executive` | Always: KPI strip, then the hero chart and the findings |
| `rail` | 1-3 KPIs with a trend: KPIs in a left column beside the hero chart |
| `band` | 3+ KPIs: KPIs on a dark header band |

Themes: `slate`, `navy`, `teal`, `plum`, `forest`, `sunset` (one accent, a highlight, neutrals,
a 10-colour categorical palette and a heatmap ramp each).

With `recipe` / `theme` on `auto` (the default), the composer picks among the arrangements that
fit the content and rotates the themes, so different dashboards look different; the same content
always gets the same look. A user or BA reviewer can pin either on the review page (a new version
that needs review). The planner never produces layouts: a new arrangement, form or theme is added
to the code and tested.

## Checking how Tableau renders a change

Judge design changes on Tableau's rendering, not the XML:

```bash
cd apps/api
.venv/Scripts/python ../../scripts/render_dashboards.py           # publish samples, save var/renders/*.png
.venv/Scripts/python ../../scripts/render_dashboards.py --only 3  # one sample
.venv/Scripts/python ../../scripts/render_dashboards.py --offline # only write the .twbx files
```

The script overwrites "PoC design preview N" workbooks in the configured non-production project.
