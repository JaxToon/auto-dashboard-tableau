"""Design tokens and arrangement choice for the generated Tableau dashboards.

The values here are generic rules distilled from well-regarded business dashboard templates
(Tableau Accelerators and Viz of the Day business dashboards): a light page with cards, one
accent colour plus neutral greys, Tableau's own font family at small sizes, no gridlines, values
labelled on the marks, KPI "BANs" with change and sparkline, and a chart form that fits the KPI
(target bars, dot plots, heatmaps, spotlight lines). No template content is copied.

The composer picks an *arrangement* (where things go) and a *theme* (how they look) from the
content, and varies them between dashboards; a user or reviewer can pin either. Both come from
fixed, tested lists: nothing here invents a layout.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

RECIPES = ("executive", "band", "rail")
THEMES = ("slate", "navy", "teal", "plum", "forest", "sunset")


@dataclass(frozen=True)
class Theme:
    name: str
    page: str  # dashboard background
    card: str  # card background
    ink: str  # primary text, KPI values
    ink_2: str  # secondary text, axis labels
    muted: str  # captions, footer
    accent: str  # main marks, section accents
    accent_soft: str  # sparklines, de-emphasised marks of the accent family
    highlight: str  # attention: below target, second series
    other: str  # neutral marks
    good: str  # change in the desired direction
    bad: str  # change in the undesired direction
    band: str  # header band (band arrangement)
    band_ink: str
    band_muted: str
    series: tuple[str, ...]  # categorical palette, in order
    font: str = "Tableau Book"
    font_strong: str = "Tableau Semibold"

    def ramp(self) -> tuple[str, str, str]:
        """Sequential colours for heatmaps: near-white to a mid tone (dark text stays readable)."""
        return (_mix(self.card, self.accent_soft, 0.25), self.accent_soft, _mix(self.accent_soft, self.accent, 0.55))


def _mix(a: str, b: str, t: float) -> str:
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(ca, cb, strict=True))


THEME_SET: dict[str, Theme] = {
    "slate": Theme("slate", page="#f2f3f5", card="#ffffff", ink="#1f2a36", ink_2="#55606b", muted="#8a939c",
                   accent="#2b5c8a", accent_soft="#b9cde0", highlight="#e8762d", other="#c3c9cf",
                   good="#2e7d4f", bad="#c2410c", band="#1f2a36", band_ink="#ffffff", band_muted="#aeb8c2",
                   series=("#2b5c8a", "#e8762d", "#5b9bd5", "#3a8f7d", "#9a7bb5", "#c44e52", "#b8a33a", "#7f8c99", "#1b998b", "#d98bb5")),
    "navy": Theme("navy", page="#eef1f6", card="#ffffff", ink="#151946", ink_2="#4a5072", muted="#8b90ad",
                  accent="#3d6fe0", accent_soft="#c3d3fa", highlight="#ff8a3d", other="#c8cde0",
                  good="#1f8a5b", bad="#d1452f", band="#151946", band_ink="#ffffff", band_muted="#a9b0d6",
                  series=("#3d6fe0", "#ff8a3d", "#22a6b3", "#7b61c9", "#e0457b", "#4caf50", "#f2b705", "#8b90ad", "#0b3c8c", "#a0522d")),
    "teal": Theme("teal", page="#eef3f2", card="#ffffff", ink="#10262b", ink_2="#3d5559", muted="#7f9693",
                  accent="#0b6e6e", accent_soft="#b5d6d3", highlight="#e8590c", other="#c6d3d1",
                  good="#0b7a4b", bad="#c2410c", band="#0f3b3d", band_ink="#ffffff", band_muted="#a3c4c1",
                  series=("#0b6e6e", "#e8590c", "#3f8fd2", "#8c6bb1", "#d4a017", "#c44e52", "#4caf7d", "#7f9693", "#1f3d7a", "#e08fb0")),
    "plum": Theme("plum", page="#f4f2f7", card="#ffffff", ink="#2a2238", ink_2="#5a5068", muted="#948aa3",
                  accent="#6b4c9a", accent_soft="#d8cdea", highlight="#e07a2f", other="#cfc8d9",
                  good="#2e7d4f", bad="#c2410c", band="#2a1f3d", band_ink="#ffffff", band_muted="#bdb2d4",
                  series=("#6b4c9a", "#e07a2f", "#2f8f9d", "#c7417b", "#5a8f29", "#3b6bc4", "#d1a21f", "#948aa3", "#8f5bd6", "#7a3b2e")),
    "forest": Theme("forest", page="#f1f5f1", card="#ffffff", ink="#1d2b21", ink_2="#4d5f52", muted="#8a9b8e",
                    accent="#2f6b3f", accent_soft="#c4dccb", highlight="#d9822b", other="#c9d6cc",
                    good="#2f6b3f", bad="#c2410c", band="#173322", band_ink="#ffffff", band_muted="#a9c4b1",
                    series=("#2f6b3f", "#d9822b", "#3a7ca5", "#8d5a97", "#b8a33a", "#c44e52", "#5fa36b", "#8a9b8e", "#1d4e89", "#d17a9a")),
    "sunset": Theme("sunset", page="#faf5f0", card="#ffffff", ink="#2d211b", ink_2="#65544a", muted="#a08e82",
                    accent="#c2552d", accent_soft="#f1d2c0", highlight="#2b6cb0", other="#e0d3c9",
                    good="#2e7d4f", bad="#b42318", band="#3b2418", band_ink="#ffffff", band_muted="#d6b9a8",
                    series=("#c2552d", "#2b6cb0", "#d9a21b", "#3f8f6b", "#8c4f9e", "#7a8b99", "#e07b91", "#a08e82", "#1f5f8b", "#6b8e23")),
}


def choose_style(recipe: str, theme: str, n_kpis: int, n_charts: int, has_trend: bool = False, seed: str = "") -> tuple[str, Theme]:
    """Resolve "auto" from the content, varying between dashboards (stable for the same content).

    - 4+ KPI cards: a dark header band or a KPI strip;
    - 1-3 KPI cards with a trend: KPIs in a left column next to the trend, a KPI strip, or (3) a band;
    - otherwise: KPI strip.
    The theme rotates over the curated list unless one is pinned.
    """
    h = int(hashlib.sha256(seed.encode()).hexdigest(), 16)
    if recipe not in RECIPES:
        if n_kpis >= 4:
            options: tuple[str, ...] = ("band", "executive")
        elif n_kpis >= 1 and has_trend and n_charts >= 1:
            options = ("rail", "executive", "band") if n_kpis == 3 else ("rail", "executive")
        else:
            options = ("executive",)
        recipe = options[h % len(options)]
    name = theme if theme in THEMES else THEMES[(h >> 16) % len(THEMES)]
    return recipe, THEME_SET[name]
