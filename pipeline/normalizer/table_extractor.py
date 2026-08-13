"""Structured parsers for specific note tables needed by the analytics layer.

Every parser returns None (never raises) when the table doesn't match the
expected structure, logging the failure via loguru.
"""
import re

from loguru import logger
from rapidfuzz import fuzz

from models.financial import (
    ActuarialAssumptions,
    CompanyRatios,
    ContingentLiabilities,
    CSRDetails,
    CWIPAgeing,
    CWIPProject,
    DisclosedRatio,
    MSMEDDisclosure,
    NoteSection,
    StructuredTables,
    TableData,
    TradePayablesAgeing,
    TradeReceivablesAgeing,
)
from utils.text_utils import parse_indian_number

_AGEING_BUCKET_ALIASES = {
    "not_due": ["not due"],
    "zero_to_six_months": ["less than 6 months", "0-6 months", "upto 6 months"],
    "six_to_twelve_months": ["6 months - 1 year", "6 months-1 year", "6-12 months"],
    "one_to_two_years": ["1-2 years", "1 - 2 years"],
    "two_to_three_years": ["2-3 years", "2 - 3 years"],
    "more_than_three_years": ["more than 3 years", "above 3 years", ">3 years"],
}


def _is_empty_table(table: TableData | None) -> bool:
    if table is None:
        return True
    if not table.headers or not table.rows:
        return True
    if all(all(cell is None or str(cell).strip() == "" for cell in row) for row in table.rows):
        return True
    return False


def _match_bucket_columns(headers: list[str]) -> dict[str, int]:
    """Map canonical ageing bucket names to header column indices via fuzzy match."""
    mapping: dict[str, int] = {}
    for bucket, aliases in _AGEING_BUCKET_ALIASES.items():
        best_score = 0
        best_idx = None
        for i, header in enumerate(headers):
            if not header:
                continue
            for alias in aliases:
                score = fuzz.partial_ratio(header.lower(), alias)
                if score > best_score:
                    best_score = score
                    best_idx = i
        if best_score > 70 and best_idx is not None:
            mapping[bucket] = best_idx
    return mapping


def _find_total_col(headers: list[str]) -> int | None:
    for i, h in enumerate(headers):
        if h and "total" in h.lower():
            return i
    return None


def parse_trade_receivables_ageing(table: TableData) -> TradeReceivablesAgeing | None:
    if _is_empty_table(table):
        logger.debug("parse_trade_receivables_ageing: empty table")
        return None
    try:
        bucket_cols = _match_bucket_columns(table.headers)
        if len(bucket_cols) < 2:
            return None
        total_col = _find_total_col(table.headers)

        values: dict[str, float | None] = {b: None for b in _AGEING_BUCKET_ALIASES}
        total_gross = None
        allowance = None
        net_total = None

        for row in table.rows:
            label = str(row[0]).lower() if row and row[0] else ""
            for bucket, col in bucket_cols.items():
                if col < len(row):
                    val = parse_indian_number(row[col])
                    if val is not None:
                        values[bucket] = val
            if "doubtful" in label or "allowance" in label or "expected credit loss" in label:
                if total_col is not None and total_col < len(row):
                    allowance = parse_indian_number(row[total_col])
            elif "net" in label and total_col is not None and total_col < len(row):
                net_total = parse_indian_number(row[total_col])
            elif ("total" in label or not label) and total_col is not None and total_col < len(row):
                val = parse_indian_number(row[total_col])
                if val is not None:
                    total_gross = val

        return TradeReceivablesAgeing(
            **values,
            total_gross=total_gross,
            allowance_doubtful=allowance,
            net_total=net_total,
            year="current",
        )
    except Exception as e:
        logger.warning(f"parse_trade_receivables_ageing failed: {e}")
        return None


def parse_trade_payables_ageing(table: TableData) -> TradePayablesAgeing | None:
    if _is_empty_table(table):
        logger.debug("parse_trade_payables_ageing: empty table")
        return None
    try:
        bucket_cols = _match_bucket_columns(table.headers)
        if len(bucket_cols) < 2:
            return None
        total_col = _find_total_col(table.headers)

        values: dict[str, float | None] = {b: None for b in _AGEING_BUCKET_ALIASES}
        total_mse = None
        total_others = None
        total = None

        for row in table.rows:
            label = str(row[0]).lower() if row and row[0] else ""
            for bucket, col in bucket_cols.items():
                if col < len(row):
                    val = parse_indian_number(row[col])
                    if val is not None:
                        values[bucket] = val
            row_total = parse_indian_number(row[total_col]) if total_col is not None and total_col < len(row) else None
            if "micro" in label or "small" in label or "mse" in label:
                total_mse = row_total
            elif "other" in label:
                total_others = row_total
            elif "total" in label:
                total = row_total

        return TradePayablesAgeing(
            **values,
            total_mse=total_mse,
            total_others=total_others,
            total=total,
            year="current",
        )
    except Exception as e:
        logger.warning(f"parse_trade_payables_ageing failed: {e}")
        return None


def parse_cwip_ageing(table: TableData) -> CWIPAgeing | None:
    if _is_empty_table(table):
        logger.debug("parse_cwip_ageing: empty table")
        return None
    try:
        bucket_headers = {
            "less_than_1_year": ["less than 1 year"],
            "1_to_2_years": ["1-2 years", "1 - 2 years"],
            "2_to_3_years": ["2-3 years", "2 - 3 years"],
            "more_than_3_years": ["more than 3 years", "above 3 years"],
        }
        col_to_bucket: dict[int, str] = {}
        for bucket, aliases in bucket_headers.items():
            for i, header in enumerate(table.headers):
                if not header:
                    continue
                if any(fuzz.partial_ratio(header.lower(), alias) > 70 for alias in aliases):
                    col_to_bucket[i] = bucket
                    break
        if not col_to_bucket:
            return None

        total_col = _find_total_col(table.headers)
        projects: list[CWIPProject] = []
        total = None

        for row in table.rows:
            label = str(row[0]).strip() if row and row[0] else None
            if label and "total" in label.lower():
                if total_col is not None and total_col < len(row):
                    total = parse_indian_number(row[total_col])
                continue
            for col, bucket in col_to_bucket.items():
                if col < len(row):
                    val = parse_indian_number(row[col])
                    if val is not None and val != 0:
                        projects.append(CWIPProject(project_name=label, age_bucket=bucket, amount=val))

        return CWIPAgeing(projects=projects, total=total, year="current")
    except Exception as e:
        logger.warning(f"parse_cwip_ageing failed: {e}")
        return None


_CONTINGENT_LABEL_TERMS = [
    "contingent", "claim", "not acknowledged", "guarantee", "undertaking",
    "income tax", "service tax", "sales tax", "vat", "gst", "duty", "demand",
]


def _looks_like_contingent_table(table: TableData) -> bool:
    """Reject unrelated tables that merely share a page with the note (note page
    ranges are page-granular, so a note can carry a neighbouring note's tables)."""
    labels = " ".join(str(row[0]).lower() for row in table.rows if row and row[0])
    return any(term in labels for term in _CONTINGENT_LABEL_TERMS)


def _find_year_cols(headers: list[str]) -> tuple[int | None, int | None]:
    """Return (current_col, prior_col) from year-bearing headers, latest year first.

    Movement schedules use a "prior | addition | deletion | current" layout, so the
    current-year column is not necessarily the last one nor prior the one after it.
    """
    years: dict[int, int] = {}  # year -> column index
    for i, header in enumerate(headers):
        found = [int(y) for y in re.findall(r"(?:19|20)\d{2}", header or "")]
        if found:
            years.setdefault(max(found), i)
    if len(years) < 2:
        return None, None
    ordered = sorted(years, reverse=True)
    return years[ordered[0]], years[ordered[1]]


def parse_contingent_liabilities(table: TableData) -> ContingentLiabilities | None:
    if _is_empty_table(table):
        logger.debug("parse_contingent_liabilities: empty table")
        return None
    if not _looks_like_contingent_table(table):
        logger.debug("parse_contingent_liabilities: labels do not look like contingent liabilities")
        return None
    try:
        current_col, prior_col = _find_year_cols(table.headers)
        if current_col is None:
            current_col = _find_total_col(table.headers)
            if current_col is None:
                current_col = len(table.headers) - 1
            prior_col = current_col + 1 if current_col + 1 < len(table.headers) else None

        income_tax = service_tax = gst = others = total = total_prior = None
        for row in table.rows:
            label = str(row[0]).lower() if row and row[0] else ""
            val = parse_indian_number(row[current_col]) if current_col < len(row) else None
            val_prior = parse_indian_number(row[prior_col]) if prior_col is not None and prior_col < len(row) else None
            if "income tax" in label:
                income_tax = val
            elif "service tax" in label:
                service_tax = val
            elif "gst" in label:
                gst = val
            elif "total" in label:
                total = val
                total_prior = val_prior
            elif label:
                others = (others or 0) + val if val else others

        if total is None and all(v is None for v in [income_tax, service_tax, gst, others]):
            return None

        return ContingentLiabilities(
            income_tax=income_tax,
            service_tax=service_tax,
            gst=gst,
            others=others,
            total=total,
            total_prior=total_prior,
        )
    except Exception as e:
        logger.warning(f"parse_contingent_liabilities failed: {e}")
        return None


def parse_csr_details(table: TableData) -> CSRDetails | None:
    if _is_empty_table(table):
        logger.debug("parse_csr_details: empty table")
        return None
    try:
        value_col = None
        for i, h in enumerate(table.headers):
            if h and re.search(r"\bamount\b|\d{4}", h.lower()):
                value_col = i
                break
        if value_col is None:
            value_col = len(table.headers) - 1

        obligation = amount_spent = amount_unspent = None
        carry_forward: list[dict] = []
        for row in table.rows:
            label = str(row[0]).lower() if row and row[0] else ""
            val = parse_indian_number(row[value_col]) if value_col < len(row) else None
            if "amount required" in label or "obligation" in label or "2%" in label:
                obligation = val
            elif "spent" in label and "unspent" not in label:
                amount_spent = val
            elif "unspent" in label:
                amount_unspent = val
            elif "carried forward" in label or "carry forward" in label:
                carry_forward.append({"label": row[0], "amount": val})

        if obligation is None and amount_spent is None and amount_unspent is None:
            return None

        return CSRDetails(
            obligation=obligation,
            amount_spent=amount_spent,
            amount_unspent=amount_unspent,
            carry_forward_years=carry_forward,
        )
    except Exception as e:
        logger.warning(f"parse_csr_details failed: {e}")
        return None


def parse_msmed_disclosure(table: TableData) -> MSMEDDisclosure | None:
    if _is_empty_table(table):
        logger.debug("parse_msmed_disclosure: empty table")
        return None
    try:
        value_col = None
        for i, h in enumerate(table.headers):
            if h and re.search(r"\bamount\b|\d{4}", h.lower()):
                value_col = i
                break
        if value_col is None:
            value_col = len(table.headers) - 1

        principal = interest_unpaid = interest_accrued = interest_further_due = None
        for row in table.rows:
            label = str(row[0]).lower() if row and row[0] else ""
            val = parse_indian_number(row[value_col]) if value_col < len(row) else None
            if "principal" in label:
                principal = val
            elif "accrued" in label and "further" not in label:
                interest_accrued = val
            elif "further" in label:
                interest_further_due = val
            elif "interest" in label:
                interest_unpaid = val

        if all(v is None for v in [principal, interest_unpaid, interest_accrued, interest_further_due]):
            return None

        return MSMEDDisclosure(
            principal_unpaid=principal,
            interest_unpaid=interest_unpaid,
            interest_accrued_unpaid=interest_accrued,
            interest_further_due=interest_further_due,
        )
    except Exception as e:
        logger.warning(f"parse_msmed_disclosure failed: {e}")
        return None


def _parse_rate_cell(cell) -> float | None:
    """Parse a cell expected to hold an actuarial assumption rate (a small percentage).

    A genuine discount rate or salary escalation rate is always well under 100%.
    A misaligned column in a poorly-ruled table can otherwise hand this a monetary
    figure from an adjacent column (this happened in testing: a rate of "14400"
    was accepted from what should have been a ~7% cell) — reject anything outside
    a plausible rate range rather than accept a clearly-wrong value.
    """
    value = parse_indian_number(cell)
    if value is None:
        return None
    if abs(value) > 30:
        logger.warning(f"Rejecting implausible actuarial rate value: {cell!r} -> {value}")
        return None
    return value


def parse_actuarial_assumptions(table: TableData) -> ActuarialAssumptions | None:
    if _is_empty_table(table):
        logger.debug("parse_actuarial_assumptions: empty table")
        return None
    try:
        current_col, prior_col = None, None
        year_cols = [i for i, h in enumerate(table.headers) if h and re.search(r"\d{4}", h)]
        if len(year_cols) >= 2:
            current_col, prior_col = year_cols[0], year_cols[1]
        elif len(table.headers) >= 3:
            current_col, prior_col = 1, 2
        else:
            return None

        discount_current = discount_prior = salary_current = salary_prior = None
        for row in table.rows:
            label = str(row[0]).lower() if row and row[0] else ""
            # Take only the first matching row for each assumption — a table covering
            # multiple plans (e.g. Gratuity and Post-Retirement Medical) can repeat
            # "discount rate" per plan, and a later row overwriting an already-valid
            # earlier one has no basis for being more "correct".
            if "discount rate" in label and discount_current is None and discount_prior is None:
                discount_current = _parse_rate_cell(row[current_col]) if current_col < len(row) else None
                discount_prior = _parse_rate_cell(row[prior_col]) if prior_col < len(row) else None
            elif "salary" in label and salary_current is None and salary_prior is None:
                salary_current = _parse_rate_cell(row[current_col]) if current_col < len(row) else None
                salary_prior = _parse_rate_cell(row[prior_col]) if prior_col < len(row) else None

        if discount_current is None and discount_prior is None:
            return None

        return ActuarialAssumptions(
            discount_rate_current=discount_current,
            discount_rate_prior=discount_prior,
            salary_escalation_shore_current=salary_current,
            salary_escalation_shore_prior=salary_prior,
        )
    except Exception as e:
        logger.warning(f"parse_actuarial_assumptions failed: {e}")
        return None


def parse_analytical_ratios(table: TableData) -> CompanyRatios | None:
    if _is_empty_table(table):
        logger.debug("parse_analytical_ratios: empty table")
        return None
    try:
        current_col = 1 if len(table.headers) > 1 else None
        prior_col = 2 if len(table.headers) > 2 else None
        variance_col = None
        for i, h in enumerate(table.headers):
            if h and "varian" in h.lower():
                variance_col = i

        ratios: list[DisclosedRatio] = []
        for row in table.rows:
            label = str(row[0]).strip() if row and row[0] else None
            if not label:
                continue
            current = parse_indian_number(row[current_col]) if current_col is not None and current_col < len(row) else None
            prior = parse_indian_number(row[prior_col]) if prior_col is not None and prior_col < len(row) else None
            variance = parse_indian_number(row[variance_col]) if variance_col is not None and variance_col < len(row) else None
            reason = None
            if len(row) > (variance_col or 0) + 1 and variance_col is not None:
                tail = row[variance_col + 1:]
                text_tail = [str(c) for c in tail if c and not parse_indian_number(c)]
                reason = " ".join(text_tail) if text_tail else None
            ratios.append(DisclosedRatio(
                name=label, current=current, prior=prior,
                variance_pct=variance, reason_for_variance=reason,
            ))

        if not ratios:
            return None
        return CompanyRatios(ratios=ratios, year="current")
    except Exception as e:
        logger.warning(f"parse_analytical_ratios failed: {e}")
        return None


_NOTE_KEYWORDS = {
    "trade_receivables_ageing": (["receivable", "debtors"], parse_trade_receivables_ageing),
    "trade_payables_ageing": (["payable", "creditors"], parse_trade_payables_ageing),
    "cwip_ageing": (["capital work-in-progress", "capital work in progress", "cwip"], parse_cwip_ageing),
    "contingent_liabilities": (["contingent liabilit"], parse_contingent_liabilities),
    "csr_details": (["corporate social responsibility", "csr"], parse_csr_details),
    "msmed_disclosure": (["micro", "small enterprise", "msme", "msmed"], parse_msmed_disclosure),
    "actuarial_assumptions": (["actuarial", "defined benefit"], parse_actuarial_assumptions),
    "company_ratios": (["ratio"], parse_analytical_ratios),
}


def extract_all_tables(notes: dict[str, NoteSection]) -> StructuredTables:
    """Run every table-specific parser over the notes, preferring notes whose
    title/text matches relevant keywords, falling back to a full scan."""
    result_kwargs: dict = {}

    for field_name, (keywords, parser) in _NOTE_KEYWORDS.items():
        matched_notes = [
            note for note in notes.values()
            if any(kw in (note.title + " " + note.raw_text[:200]).lower() for kw in keywords)
        ]
        candidate_notes = matched_notes or list(notes.values())

        parsed = None
        for note in candidate_notes:
            for table in note.tables:
                parsed = parser(table)
                if parsed is not None:
                    break
            if parsed is not None:
                break

        result_kwargs[field_name] = parsed

    return StructuredTables(**result_kwargs)
