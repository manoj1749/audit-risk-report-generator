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
    PPEDepreciationRollforward,
    StructuredTables,
    TableData,
    TradePayablesAgeing,
    TradeReceivablesAgeing,
    TradeReceivablesECLSummary,
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


_ECL_ALLOWANCE_LABEL = re.compile(
    r"allowance\s+for\s+(?:doubtful\s+debts|expected\s+credit\s+loss)|"
    r"provision\s+for\s+doubtful\s+debts",
    re.IGNORECASE,
)
_ECL_GROSS_LABEL = re.compile(r"^total$|^total\s+trade\s+receivable", re.IGNORECASE)


def parse_trade_receivables_ecl_summary(table: TableData) -> TradeReceivablesECLSummary | None:
    """A simple 2-year comparative table inside the trade receivables note
    (distinct from the multi-bucket ageing schedule): a 'Particulars' label
    column plus exactly two value columns (current year, prior year), with
    a row labeled like 'Allowance for doubtful debts'. Confirmed against a
    real filing's Note 7(e) breakdown (SCI FY24-25)."""
    if _is_empty_table(table):
        return None
    try:
        value_cols = [i for i in range(1, len(table.headers)) if table.headers[i]]
        if len(value_cols) != 2:
            return None
        current_col, prior_col = value_cols[0], value_cols[1]

        allowance_current = allowance_prior = None
        gross_current = gross_prior = None
        for row in table.rows:
            if not row or not row[0]:
                continue
            label = str(row[0]).strip()
            if _ECL_ALLOWANCE_LABEL.search(label):
                if current_col < len(row):
                    allowance_current = parse_indian_number(row[current_col])
                if prior_col < len(row):
                    allowance_prior = parse_indian_number(row[prior_col])
            elif _ECL_GROSS_LABEL.match(label) and gross_current is None:
                if current_col < len(row):
                    gross_current = parse_indian_number(row[current_col])
                if prior_col < len(row):
                    gross_prior = parse_indian_number(row[prior_col])

        if allowance_current is None or allowance_prior is None:
            return None

        return TradeReceivablesECLSummary(
            allowance_current=allowance_current, allowance_prior=allowance_prior,
            gross_current=gross_current, gross_prior=gross_prior,
        )
    except Exception as e:
        logger.warning(f"parse_trade_receivables_ecl_summary failed: {e}")
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


_PPE_SECTION_HEADER_ALIASES = {
    "dep": ["depreciation and impairment", "accumulated depreciation"],
    "gross": ["gross block"],
    "nbv": ["net book value", "carrying amount"],
}


def _ppe_section_header(label: str) -> str | None:
    low = label.lower().strip().rstrip(":")
    for section, aliases in _PPE_SECTION_HEADER_ALIASES.items():
        if any(fuzz.ratio(low, alias) > 85 for alias in aliases):
            return section
    return None


# By the time a table reaches this parser, pdfplumber's per-line cell content
# has already been flattened to a single space-joined string (see
# pdf_extractor._fix_reversed_cell_text usage), so the original row
# boundaries within a merged label/value cell can't be recovered by
# splitting on newlines. Reconstruct them instead by matching the known,
# small vocabulary of row labels this Schedule III note uses -- the phrases
# themselves double as the split points.
_PPE_LABEL_TOKEN_PATTERN = re.compile(
    r"gross block|"
    r"deemed cost as at [a-z]+\s+\d{1,2},?\s*\d{4}|"
    r"additions?|"
    r"disposals?(?:\s*/\s*adjustments?)?|"
    r"depreciation and impairment:?|"
    r"accumulated depreciation|"
    r"depreciation charge for the year|"
    r"depreciation for the year|"
    r"(?:as\s+)?at\s+[a-z]+\s+\d{1,2},?\s*\d{4}|"
    r"net book value:?|"
    r"carrying amount",
    re.IGNORECASE,
)
_PPE_VALUE_TOKEN_PATTERN = re.compile(r"\(?-?[\d,]*\.?\d+\)?|-(?!\d)")


def parse_ppe_depreciation_rollforward(table: TableData) -> PPEDepreciationRollforward | None:
    """Property, plant & equipment note: reconstruct the row sequence from
    the flattened label/value cell text using known row-label phrases as
    split points, then walk labels and values in lockstep, treating
    section-header phrases (which have no value of their own) as state."""
    if _is_empty_table(table):
        return None
    try:
        total_col = _find_total_col(table.headers)
        if total_col is None and len(table.headers) > 1:
            total_col = len(table.headers) - 1
        if total_col is None:
            return None

        labels: list[str] = []
        values: list[str] = []
        for row in table.rows:
            if not row or total_col >= len(row):
                continue
            label_cell = str(row[0] or "")
            value_cell = str(row[total_col] or "")
            labels.extend(m.group(0) for m in _PPE_LABEL_TOKEN_PATTERN.finditer(label_cell))
            values.extend(m.group(0) for m in _PPE_VALUE_TOKEN_PATTERN.finditer(value_cell))

        section = None
        dep_rows: list[tuple[str, str]] = []
        vi = 0
        for label in labels:
            label = label.strip()
            if not label:
                continue
            hdr = _ppe_section_header(label)
            if hdr:
                section = hdr
                continue
            if vi >= len(values):
                break
            if section == "dep":
                dep_rows.append((label, values[vi]))
            vi += 1

        dated_idxs = [i for i, (l, _) in enumerate(dep_rows) if l.lower().startswith(("at ", "as at"))]
        if len(dated_idxs) < 2:
            return None
        open_idx, close_idx = dated_idxs[-2], dated_idxs[-1]
        opening = parse_indian_number(dep_rows[open_idx][1])
        closing = parse_indian_number(dep_rows[close_idx][1])

        charge = 0.0
        disposals = 0.0
        found_charge = False
        for label, value in dep_rows[open_idx + 1:close_idx]:
            val = parse_indian_number(value)
            if val is None:
                continue
            low = label.lower()
            if "charge" in low or "depreciation for the year" in low:
                charge += val
                found_charge = True
            elif "disposal" in low or "adjustment" in low:
                disposals += val

        if opening is None or closing is None or not found_charge:
            return None

        return PPEDepreciationRollforward(
            opening_accumulated_depreciation=opening,
            depreciation_charge=charge,
            disposals=disposals,
            closing_accumulated_depreciation=closing,
        )
    except Exception as e:
        logger.warning(f"parse_ppe_depreciation_rollforward failed: {e}")
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
                # Confirmed real false positive (CESCOM): a bare "accrued" match
                # picked up an unrelated "Accrued expenses and other liabilities"
                # row from a broader liabilities note that only incidentally also
                # mentions MSME elsewhere -- genuine MSMED Act disclosure rows are
                # always phrased as "interest accrued", never bare "accrued".
                if "interest" in label:
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


# The Schedule III / Companies Act ratio disclosure is a standardized,
# government-mandated list of exactly 11 named ratios (confirmed verbatim
# against a real filing's own Note 34: Current ratio, Debt Equity ratio,
# Debt service coverage ratio, Return on Equity ratio, Inventory Turnover
# ratio, Trade receivables Turnover ratio, Trade Payables Turnover ratio,
# Net Capital Turnover Ratio, Net Profit Ratio, Return on Capital Employed,
# Return on Investment). Without checking a table's row labels actually
# look like these, extract_all_tables' keyword-match-fails-so-scan-every-
# note fallback (see _NOTE_KEYWORDS below) causes this parser to silently
# "succeed" on ANY unrelated 2-3-column note table (confirmed: a broken
# note-boundary detection swallowed the real ratios note into an earlier,
# wrongly-titled note's span, so keyword matching found nothing, and this
# parser then grabbed a completely unrelated revenue-breakdown note's
# table and reported its row labels as if they were ratio names).
_KNOWN_RATIO_NAME_TERMS = (
    "current ratio", "quick ratio", "debt equity", "debt-equity", "debt service coverage",
    "return on equity", "inventory turnover", "receivables turnover", "payables turnover",
    "capital turnover", "net profit ratio", "net profit margin", "return on capital employed",
    "return on investment", "roce", "roe",
)
_MIN_RECOGNIZED_RATIO_ROWS = 2
_SERIAL_COL_HEADER_PATTERN = re.compile(r"^(sr\.?\s*no\.?|s\.?\s*no\.?|sl\.?\s*no\.?|#)$", re.IGNORECASE)


def parse_analytical_ratios(table: TableData) -> CompanyRatios | None:
    if _is_empty_table(table):
        logger.debug("parse_analytical_ratios: empty table")
        return None
    try:
        # The Schedule III ratio table commonly has a leading serial-number
        # column ("Sr No") before the ratio name -- confirmed on a real
        # filing's own Note 34, where the label column is index 1, not 0.
        label_col = 0
        for i, h in enumerate(table.headers):
            if h and _SERIAL_COL_HEADER_PATTERN.match(h.strip()):
                label_col = i + 1
                break
        current_col = label_col + 1 if len(table.headers) > label_col + 1 else None
        prior_col = label_col + 2 if len(table.headers) > label_col + 2 else None
        variance_col = None
        for i, h in enumerate(table.headers):
            if h and "varian" in h.lower():
                variance_col = i

        ratios: list[DisclosedRatio] = []
        for row in table.rows:
            label = str(row[label_col]).strip() if row and label_col < len(row) and row[label_col] else None
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
        recognized = sum(
            1 for r in ratios
            if any(term in r.name.lower() for term in _KNOWN_RATIO_NAME_TERMS)
        )
        if recognized < _MIN_RECOGNIZED_RATIO_ROWS:
            logger.debug(
                f"parse_analytical_ratios: only {recognized} row(s) matched known ratio "
                "names, rejecting table as not a genuine ratios disclosure"
            )
            return None
        return CompanyRatios(ratios=ratios, year="current")
    except Exception as e:
        logger.warning(f"parse_analytical_ratios failed: {e}")
        return None


_NOTE_KEYWORDS = {
    "trade_receivables_ageing": (["receivable", "debtors"], parse_trade_receivables_ageing),
    "trade_receivables_ecl": (["receivable", "debtors"], parse_trade_receivables_ecl_summary),
    "trade_payables_ageing": (["payable", "creditors"], parse_trade_payables_ageing),
    "cwip_ageing": (["capital work-in-progress", "capital work in progress", "cwip"], parse_cwip_ageing),
    "ppe_depreciation": (["property, plant and equipment", "property plant and equipment"], parse_ppe_depreciation_rollforward),
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
        # Word-boundary match, not bare substring containment -- confirmed a
        # real false positive on "ratio" matching inside "Operations"
        # (op-e-RATIO-ns), which wrongly selected an unrelated note as the
        # sole candidate and prevented the real fallback full-scan from ever
        # running.
        keyword_patterns = [re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE) for kw in keywords]
        matched_notes = [
            note for note in notes.values()
            if any(p.search(note.title + " " + note.raw_text[:200]) for p in keyword_patterns)
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
