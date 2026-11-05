# Tableau dashboard design system

The generated workbook (`apps/api/app/tableau_artifact.py`) follows a small, fixed design system
defined in `apps/api/app/tableau_theme.py`. The rules were distilled from well-regarded business
dashboard templates (Tableau Accelerators and business-themed Viz of the Day dashboards), by
parsing their workbook XML and looking at their thumbnails. No template content is copied or
shipped; only the generic rules below are used. Reference workbooks are kept outside the repository.

## Rules every dashboard follows

| Area | Rule |
|---|---|
| Page | Light grey page, white cards with 12-16 px padding, 12 px gaps, 20 px outer margin, fixed 1400 px width; height follows the content. |
| Type | Tableau's own family: *Tableau Semibold* for titles and KPI values, *Tableau Book* for text. Title 20 pt, card titles 11 pt, body 9-10 pt, KPI value 24 pt. |
| Colour | Neutral greys plus one accent colour. A second colour only for emphasis (top N) or for the direction of a change. Series use the theme palette, largest series first. |
| Worksheets | No gridlines, zero lines, row/column dividers or axis ticks; field labels hidden. Bars show their value as a label and hide the value axis. Axis titles are removed; the card title says what is measured. |
| Lines | Up to 6 series are labelled at the line ends (no legend); more series get a compact legend inside the card. |
| KPI cards (BANs) | Short label, big value, change vs the previous period (▲/▼, coloured by whether the change is good, using the catalog's `higher_is_better`), and a sparkline. Growth-rate KPIs show no period-over-period change. |
| Findings | A "Key findings" card with the deterministic insights (all filters on All). |
| Compliance | The "POC / SYNTHETIC DATA" notice is part of every header, and the provenance footer is on every dashboard. |

All values stay computed by Tableau from the embedded additive KPI parts, so the change and the
sparkline follow the dashboard filters like every other number.

## Recipes (arrangement) and themes (look)

| Recipe | Picked automatically when | Default theme |
|---|---|---|
| `band` | 3 or more KPI cards: the KPIs sit on a dark header band | `navy` |
| `rail` | 1-2 KPI cards and 2 or more charts: KPIs and findings in a left rail | `teal` |
| `executive` | Otherwise: KPI row with findings beside it, chart grid; a single chart gets the findings next to it | `slate` |

`DashboardSpec.recipe` and `DashboardSpec.theme` default to `auto`. A user or BA reviewer can pick
another fixed recipe or theme on the review page (it creates a new version that needs review). The
planner never produces layouts; adding one means adding a tested recipe to `tableau_theme.py` and
the layout code.

## Checking how Tableau renders a change

Judge design changes on Tableau's rendering, not the XML:

```bash
cd apps/api
.venv/Scripts/python ../../scripts/render_dashboards.py          # publish samples, save var/renders/*.png
.venv/Scripts/python ../../scripts/render_dashboards.py --offline # only write the .twbx files
```

The script overwrites "PoC design preview N" workbooks in the configured non-production project.
