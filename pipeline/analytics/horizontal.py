"""Horizontal analysis: YoY absolute and percentage movements per canonical line item."""
from models.financial import MappedLineItem, MovementRecord
from pipeline.normalizer.schema import DISPLAY_LABELS


_METHOD_PRIORITY = {"exact": 3, "structural": 2, "fuzzy": 2, "embedding": 1, "unknown": 0}


def _select_best(items: list[MappedLineItem]) -> MappedLineItem:
    """Prefer an actual current value, then a candidate with BOTH current and
    prior over one with only current — an exact match missing the prior
    year is less useful for movement analysis than a fuzzy match that has
    both, so completeness is ranked above match provenance, not after it —
    then exact matches over fuzzy/embedding ones, then confidence."""
    with_current = [i for i in items if i.current_value is not None]
    pool = with_current or items
    return max(pool, key=lambda i: (
        i.current_value is not None and i.prior_value is not None,
        _METHOD_PRIORITY.get(i.method, 0),
        i.confidence,
    ))


def get_canonical_values(mapped_items: dict[str, MappedLineItem]) -> dict[str, MappedLineItem]:
    """Group mapped items by canonical_key and pick the single best representative for each."""
    grouped: dict[str, list[MappedLineItem]] = {}
    for item in mapped_items.values():
        if not item.canonical_key:
            continue
        grouped.setdefault(item.canonical_key, []).append(item)
    return {key: _select_best(items) for key, items in grouped.items()}


# No single face-statement line item's prior-year figure should sensibly
# exceed the CURRENT year's total assets by more than this multiple -- e.g.
# a "prior" of Rs 27,970 crore against Rs 17.8 crore of total assets today
# is not a real 99% YoY decline, it's the prior-year cell having been
# sourced from an unrelated row (confirmed on a real filing: a note's own
# nested currency-breakdown sub-table with its own "Total"/lettered subtotal
# rows bled into the face-statement scan). Real, economically plausible
# swings don't land here; this exists to catch extraction cross-contamination,
# not to second-guess genuine large movements.
_IMPLAUSIBLE_PRIOR_MULTIPLE = 5.0


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

        implausible_prior = (
            total_assets is not None and prior is not None
            and abs(prior) > total_assets * _IMPLAUSIBLE_PRIOR_MULTIPLE
        )
        if implausible_prior:
            prior = None

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
            note_ref=item.note_ref,
            prior_suppressed=implausible_prior,
        )
    return movements
