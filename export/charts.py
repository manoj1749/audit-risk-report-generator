"""Chart generation for the audit report: risk distribution, key movements, ratios.

Every chart is rendered from the same deterministic data structures used
elsewhere in the report (summary counts, MovementRecord, ComputedRatios) —
charts visualize numbers already computed by Layers 1-4, nothing here
touches the LLM output or introduces new figures.
"""
import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from models.financial import ComputedRatios, MovementRecord

_RISK_COLORS = {"High": "#C0392B", "Medium": "#D68910", "Low": "#229954"}
_DECLINE_COLOR = "#C0392B"
_INCREASE_COLOR = "#2E7D32"


def _fig_to_png_bytes(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def generate_risk_distribution_chart(summary: dict) -> bytes | None:
    """Pie chart of observation counts by risk rating."""
    labels = [k for k in ("High", "Medium", "Low") if summary.get(k, 0) > 0]
    if not labels:
        return None
    sizes = [summary[k] for k in labels]
    colors = [_RISK_COLORS[k] for k in labels]

    fig, ax = plt.subplots(figsize=(5, 4))
    ax.pie(
        sizes,
        labels=[f"{k} ({v})" for k, v in zip(labels, sizes)],
        colors=colors,
        autopct="%1.0f%%",
        startangle=90,
        wedgeprops={"edgecolor": "white", "linewidth": 1.5},
        textprops={"fontsize": 10},
    )
    ax.set_title("Observations by Risk Rating", fontsize=12, fontweight="bold")
    ax.axis("equal")
    return _fig_to_png_bytes(fig)


def generate_movements_chart(key_movements: list[MovementRecord], top_n: int = 12) -> bytes | None:
    """Horizontal bar chart of the largest YoY % movements, red for declines, green for increases."""
    ranked = sorted(
        (m for m in key_movements if m.pct_change is not None),
        key=lambda m: abs(m.pct_change),
        reverse=True,
    )[:top_n]
    if not ranked:
        return None
    ranked = list(reversed(ranked))  # largest at top of horizontal bar chart

    labels = [m.display_label for m in ranked]
    values = [m.pct_change for m in ranked]
    colors = [_INCREASE_COLOR if v >= 0 else _DECLINE_COLOR for v in values]

    fig, ax = plt.subplots(figsize=(7, max(3, 0.4 * len(ranked))))
    bars = ax.barh(labels, values, color=colors)
    ax.axvline(0, color="#333333", linewidth=0.8)
    ax.set_xlabel("YoY Change (%)", fontsize=10)
    ax.set_title("Key Financial Movements", fontsize=12, fontweight="bold")
    ax.tick_params(axis="y", labelsize=9)

    for bar, val in zip(bars, values):
        offset = (max(abs(v) for v in values) * 0.02) or 1
        x = bar.get_width() + (offset if val >= 0 else -offset)
        ha = "left" if val >= 0 else "right"
        ax.text(x, bar.get_y() + bar.get_height() / 2, f"{val:+.0f}%", va="center", ha=ha, fontsize=8)

    fig.tight_layout()
    return _fig_to_png_bytes(fig)


def generate_ratios_chart(ratios: ComputedRatios) -> bytes | None:
    """Grouped bar chart comparing current vs prior period for the core ratios."""
    rows = [
        ("Current Ratio", ratios.current_ratio_current, ratios.current_ratio_prior),
        ("Quick Ratio", ratios.quick_ratio_current, ratios.quick_ratio_prior),
        ("Debt-Equity", ratios.debt_equity_current, ratios.debt_equity_prior),
        ("Interest Coverage", ratios.interest_coverage_current, ratios.interest_coverage_prior),
        ("CFO / PAT", ratios.cfo_to_pat_current, ratios.cfo_to_pat_prior),
    ]
    rows = [r for r in rows if r[1] is not None or r[2] is not None]
    if not rows:
        return None

    labels = [r[0] for r in rows]
    current_vals = [r[1] if r[1] is not None else 0 for r in rows]
    prior_vals = [r[2] if r[2] is not None else 0 for r in rows]

    x = range(len(labels))
    width = 0.35

    fig, ax = plt.subplots(figsize=(7, 4))
    ax.bar([i - width / 2 for i in x], current_vals, width, label="Current", color="#2E5A9C")
    ax.bar([i + width / 2 for i in x], prior_vals, width, label="Prior", color="#A9B7C6")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, fontsize=9, rotation=15, ha="right")
    ax.set_title("Current vs Prior Period Ratios", fontsize=12, fontweight="bold")
    ax.legend(fontsize=9)
    ax.axhline(0, color="#333333", linewidth=0.8)
    fig.tight_layout()
    return _fig_to_png_bytes(fig)
