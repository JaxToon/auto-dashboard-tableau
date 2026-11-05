"""Design tokens and layout recipes for the generated Tableau dashboards.

The values here are generic rules distilled from well-regarded business dashboard templates
(Tableau Accelerators and Viz of the Day business dashboards): a light page with white cards,
one accent colour plus neutral greys, Tableau's own font family at small sizes, no gridlines,
zero lines or row dividers, values labelled on the marks instead of read off an axis, and KPI
"BANs" (big number, short label, change vs the previous period, sparkline). No template content
is copied; only these rules are used.

A *recipe* decides where things go; a *theme* decides how they look. Both are fixed, tested
choices: the planner (or a reviewer) can pick one, but never invent new layouts.
"""

from __future__ import annotations

from dataclasses import dataclass

RECIPES = ("executive", "band", "rail")
THEMES = ("slate", "navy", "teal")


@dataclass(frozen=True)
class Theme:
    name: str
    page: str  # dashboard background
    card: str  # card background
    ink: str  # primary text, KPI values
    ink_2: str  # secondary text, axis labels
    muted: str  # captions, footer
    accent: str  # single-series marks, section titles
    accent_soft: str  # sparklines, secondary marks
    highlight: str  # "top N" emphasis
    other: str  # de-emphasised marks
    good: str  # change in the desired direction
    bad: str  # change in the undesired direction
    band: str  # header band (band recipe)
    band_ink: str
    band_muted: str
    series: tuple[str, ...]  # categorical palette, in order
    font: str = "Tableau Book"
    font_light: str = "Tableau Light"
    font_strong: str = "Tableau Semibold"


THEME_SET: dict[str, Theme] = {
    # Accelerator-style: grey page, white cards, steel-blue accent, orange for emphasis.
    "slate": Theme("slate", page="#f2f3f5", card="#ffffff", ink="#1f2a36", ink_2="#55606b", muted="#8a939c",
                   accent="#2b5c8a", accent_soft="#b9cde0", highlight="#e8762d", other="#c3c9cf",
                   good="#2e7d4f", bad="#c2410c", band="#1f2a36", band_ink="#ffffff", band_muted="#aeb8c2",
                   series=("#2b5c8a", "#e8762d", "#5b9bd5", "#3a8f7d", "#9a7bb5", "#c44e52", "#b8a33a", "#7f8c99", "#1b998b", "#d98bb5")),
    # Dark navy header band, bright blue accent.
    "navy": Theme("navy", page="#eef1f6", card="#ffffff", ink="#151946", ink_2="#4a5072", muted="#8b90ad",
                  accent="#3d6fe0", accent_soft="#c3d3fa", highlight="#ff8a3d", other="#c8cde0",
                  good="#1f8a5b", bad="#d1452f", band="#151946", band_ink="#ffffff", band_muted="#a9b0d6",
                  series=("#3d6fe0", "#ff8a3d", "#22a6b3", "#7b61c9", "#e0457b", "#4caf50", "#f2b705", "#8b90ad", "#0b3c8c", "#a0522d")),
    # Deep teal (the app's own palette) with an orange signal.
    "teal": Theme("teal", page="#eef3f2", card="#ffffff", ink="#10262b", ink_2="#3d5559", muted="#7f9693",
                  accent="#0b6e6e", accent_soft="#b5d6d3", highlight="#e8590c", other="#c6d3d1",
                  good="#0b7a4b", bad="#c2410c", band="#0f3b3d", band_ink="#ffffff", band_muted="#a3c4c1",
                  series=("#0b6e6e", "#e8590c", "#3f8fd2", "#8c6bb1", "#d4a017", "#c44e52", "#4caf7d", "#7f9693", "#1f3d7a", "#e08fb0")),
}

RECIPE_DEFAULT_THEME = {"executive": "slate", "band": "navy", "rail": "teal"}


def choose_style(recipe: str, theme: str, n_kpis: int, n_charts: int) -> tuple[str, Theme]:
    """Resolve "auto" choices from the shape of the content.

    - three or more KPIs: a header band carries the KPIs (they are the headline);
    - one or two KPIs next to two or more charts: KPIs in a left rail, charts on the right;
    - otherwise (few KPIs, or a single chart): executive grid with findings beside the content.
    """
    if recipe not in RECIPES:
        if n_kpis >= 3:
            recipe = "band"
        elif n_kpis >= 1 and n_charts >= 2:
            recipe = "rail"
        else:
            recipe = "executive"
    name = theme if theme in THEMES else RECIPE_DEFAULT_THEME[recipe]
    return recipe, THEME_SET[name]
