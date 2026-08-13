"""Ratio computation from mapped line items. All divisions are safe-divided."""
from models.financial import ComputedRatios, MappedLineItem
from pipeline.analytics.horizontal import get_canonical_values


def _safe_div(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _val(values: dict, key: str, period: str) -> float | None:
    item = values.get(key)
    if item is None:
        return None
    return item.current_value if period == "current" else item.prior_value


def _compute_period(values: dict, period: str) -> dict[str, float | None]:
    total_current_assets = _val(values, "total_current_assets", period)
    total_current_liabilities = _val(values, "total_current_liabilities", period)
    inventories = _val(values, "inventories", period)
    borrowings_current = _val(values, "borrowings_current", period)
    borrowings_non_current = _val(values, "borrowings_non_current", period)
    total_equity = _val(values, "total_equity", period)
    pbt = _val(values, "pbt", period)
    finance_costs = _val(values, "finance_costs", period)
    trade_receivables = _val(values, "trade_receivables", period)
    revenue = _val(values, "revenue_from_operations", period)
    trade_payables_mse = _val(values, "trade_payables_mse", period)
    trade_payables_others = _val(values, "trade_payables_others", period)
    cost_of_services = _val(values, "cost_of_services", period)
    pat = _val(values, "pat", period)
    total_assets = _val(values, "total_assets", period)
    cfo = _val(values, "cfo", period)

    total_debt = None
    if borrowings_current is not None or borrowings_non_current is not None:
        total_debt = (borrowings_current or 0) + (borrowings_non_current or 0)

    total_trade_payables = None
    if trade_payables_mse is not None or trade_payables_others is not None:
        total_trade_payables = (trade_payables_mse or 0) + (trade_payables_others or 0)

    ebit = None
    if pbt is not None and finance_costs is not None:
        ebit = pbt + finance_costs

    capital_employed = None
    if total_assets is not None and total_current_liabilities is not None:
        capital_employed = total_assets - total_current_liabilities

    return {
        "current_ratio": _safe_div(total_current_assets, total_current_liabilities),
        "quick_ratio": _safe_div(
            (total_current_assets - inventories)
            if total_current_assets is not None and inventories is not None
            else None,
            total_current_liabilities,
        ),
        "debt_equity": _safe_div(total_debt, total_equity),
        "interest_coverage": _safe_div(ebit, finance_costs),
        "debtor_days": _safe_div(trade_receivables, revenue),
        "creditor_days": _safe_div(total_trade_payables, cost_of_services),
        "net_profit_margin": _safe_div(pat, revenue),
        "roce": _safe_div(ebit, capital_employed),
        "cfo_to_pat": _safe_div(cfo, pat),
    }


def _scale(value: float | None, factor: float) -> float | None:
    return value * factor if value is not None else None


def compute_ratios(mapped_items: dict[str, MappedLineItem]) -> ComputedRatios:
    values = get_canonical_values(mapped_items)
    current = _compute_period(values, "current")
    prior = _compute_period(values, "prior")

    return ComputedRatios(
        current_ratio_current=current["current_ratio"],
        current_ratio_prior=prior["current_ratio"],
        quick_ratio_current=current["quick_ratio"],
        quick_ratio_prior=prior["quick_ratio"],
        debt_equity_current=current["debt_equity"],
        debt_equity_prior=prior["debt_equity"],
        interest_coverage_current=current["interest_coverage"],
        interest_coverage_prior=prior["interest_coverage"],
        debtor_days_current=_scale(current["debtor_days"], 365),
        debtor_days_prior=_scale(prior["debtor_days"], 365),
        creditor_days_current=_scale(current["creditor_days"], 365),
        creditor_days_prior=_scale(prior["creditor_days"], 365),
        net_profit_margin_current=_scale(current["net_profit_margin"], 100),
        net_profit_margin_prior=_scale(prior["net_profit_margin"], 100),
        roce_current=_scale(current["roce"], 100),
        roce_prior=_scale(prior["roce"], 100),
        cfo_to_pat_current=current["cfo_to_pat"],
        cfo_to_pat_prior=prior["cfo_to_pat"],
    )
