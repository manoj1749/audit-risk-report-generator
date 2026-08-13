"""Horizontal analysis: YoY absolute and percentage movements per canonical line item."""
from models.financial import MappedLineItem, MovementRecord
from pipeline.normalizer.schema import DISPLAY_LABELS


_METHOD_PRIORITY = {"exact": 3, "fuzzy": 2, "embedding": 1, "unknown": 0}


def _select_best(items: list[MappedLineItem]) -> MappedLineItem:
    """Prefer an actual current value, then exact matches over fuzzy/embedding ones, then confidence."""
    with_current = [i for i in items if i.current_value is not None]
    pool = with_current or items
    return max(pool, key=lambda i: (_METHOD_PRIORITY.get(i.method, 0), i.confidence))


def get_canonical_values(mapped_items: dict[str, MappedLineItem]) -> dict[str, MappedLineItem]:
    """Group mapped items by canonical_key and pick the single best representative for each."""
    grouped: dict[str, list[MappedLineItem]] = {}
    for item in mapped_items.values():
        if not item.canonical_key:
            continue
        grouped.setdefault(item.canonical_key, []).append(item)
    return {key: _select_best(items) for key, items in grouped.items()}


def compute_movements(mapped_items: dict[str, MappedLineItem]) -> dict[str, MovementRecord]:
    """Compute YoY movements for all canonical keys where at least one of current/prior is non-None."""
    best_by_key = get_canonical_values(mapped_items)

    total_assets_item = best_by_key.get("total_assets")
    total_assets = total_assets_item.current_value if total_assets_item else None

    movements: dict[str, MovementRecord] = {}
    for key, item in best_by_key.items():
        current = item.current_value
        prior = item.prior_value
        if current is None and prior is None:
            continue

        absolute_change = (current - prior) if current is not None and prior is not None else None
        pct_change = None
        if current is not None and prior not in (None, 0):
            pct_change = (current - prior) / abs(prior) * 100

        materiality_pct = None
        if current is not None and total_assets:
            materiality_pct = abs(current) / total_assets * 100

        movements[key] = MovementRecord(
            canonical_key=key,
            display_label=DISPLAY_LABELS.get(key, key.replace("_", " ").title()),
            current=current,
            prior=prior,
            absolute_change=absolute_change,
            pct_change=pct_change,
            materiality_pct=materiality_pct,
        )
    return movements
