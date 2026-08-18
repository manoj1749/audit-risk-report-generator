"""Cross-note consistency checks, run after all note tables are parsed."""
import re

from loguru import logger

from models.financial import CSRDetails, NoteSection, StructuredTables
from models.flags import AuditFlag
from pipeline.normalizer.line_item_mapper import _parse_table_rows

_RESTRICTED_BALANCE_PATTERN = re.compile(r"restricted[^\n\d]{0,60}", re.IGNORECASE)
_NUMBER_PATTERN = re.compile(r"[\d,]+\.?\d*")

# CIN (Corporate Identification Number) is a unique 21-character identifier
# every Indian company has: 1 letter (L/U) + 5-digit activity code + 2-letter
# state code + 4-digit incorporation year + 3-letter ownership type (PLC,
# PTC, GOI, etc.) + 6-digit registration number.
_CIN_PATTERN = re.compile(r"\b[LUu]\d{5}[A-Za-z]{2}\d{4}[A-Za-z]{3}\d{6}\b")

# A CIN cited inside a Form AOC-2 related-party-transactions table is a
# routine disclosure about a DIFFERENT company (a parent, sister concern,
# trustee) — not evidence this document bundles that company's own
# financial statements. Confirmed on a real single-entity filing: 3
# unrelated real companies' CINs all appeared inside one such table,
# producing a false positive. "related party" alone isn't a reliable enough
# marker: pdfplumber's text extraction interleaves this table's two columns
# in a scrambled order (confirmed — "Name(s) of the related IDBI MF Trustee
# Company Limited (CIN: | party and nature of U6599...)" splits "related"
# and "party" apart with the company name/CIN injected between them), so
# "related\s+party" as an adjacent phrase missed 2 of the 3. AOC-2's
# section header phrase "arm's length basis" survives that scrambling
# intact in all 3 real cases, since it's a fixed heading rather than part
# of the two-column data itself — a bare occurrence-count threshold can't
# substitute for this either, since the filing's own CIN appeared 43 times
# but a genuine second bundled entity can legitimately appear just once
# (e.g. only on its own signature page).
_RELATED_PARTY_CONTEXT = re.compile(r"arm'?s\s+length|related\s+part(?:y|ies)", re.IGNORECASE)
_RELATED_PARTY_WINDOW = 300


def check_multi_entity_document(full_text: str) -> AuditFlag | None:
    """This tool's whole analysis assumes one reporting entity. >1 distinct
    CIN in a document (excluding ones cited in a related-party disclosure —
    see _RELATED_PARTY_CONTEXT) is near-certain proof it isn't one —
    confirmed on a real filing where a 247-page mutual-fund AMC annual
    report turned out to bundle 19 separate SEBI-registered fund schemes,
    each with its own financial statements. The mapper had no way to tell
    which scheme a given figure belonged to, producing a low mapping rate
    and at least one flag that silently mixed two different schemes'
    figures under one label. Rather than repeat that silently, surface it
    as the loudest possible warning so every other observation in the
    report gets read with the right skepticism."""
    cins = set()
    for m in _CIN_PATTERN.finditer(full_text):
        window_start = max(0, m.start() - _RELATED_PARTY_WINDOW)
        if _RELATED_PARTY_CONTEXT.search(full_text[window_start:m.start()]):
            continue
        cins.add(m.group(0).upper())
    cins = sorted(cins)
    if len(cins) > 1:
        return AuditFlag(
            flag_id="MULTI_ENTITY_DOCUMENT",
            area="Document Scope",
            severity="High",
            evidence={"distinct_cins": cins, "count": len(cins)},
            note_ids=[],
            standard_query="single reporting entity separate financial statements Ind AS 1",
            triggered_by=f"Document contains {len(cins)} distinct company CINs — likely a multi-entity bundle",
        )
    return None


def check_csr_balance_vs_bank(
    csr_details: CSRDetails | None, note_7f_restricted_balance: float | None
) -> AuditFlag | None:
    """CSR unspent balance should match the restricted bank balance disclosed elsewhere."""
    if csr_details and csr_details.amount_unspent is not None and note_7f_restricted_balance is not None:
        diff = abs(csr_details.amount_unspent - note_7f_restricted_balance)
        if diff > 1:
            return AuditFlag(
                flag_id="XNOTE_CSR_BANK_MISMATCH",
                area="Data Consistency – CSR Bank Balance",
                severity="Low",
                evidence={
                    "csr_note_unspent": csr_details.amount_unspent,
                    "bank_note_restricted": note_7f_restricted_balance,
                    "difference": diff,
                },
                note_ids=["25b", "7f"],
                standard_query="CSR unspent bank balance reconciliation",
                triggered_by="CSR unspent in Note 25b does not match restricted balance in Note 7f",
            )
    return None


def check_lease_rou_consistency(
    lease_liability_total: float | None, rou_gross_additions: float | None
) -> AuditFlag | None:
    """New lease additions should broadly match new lease liabilities recognised."""
    if lease_liability_total and rou_gross_additions and rou_gross_additions > 0:
        diff_pct = abs(lease_liability_total - rou_gross_additions) / rou_gross_additions * 100
        if diff_pct > 15:
            return AuditFlag(
                flag_id="XNOTE_LEASE_ROU_MISMATCH",
                area="Data Consistency – Lease ROU vs Liability",
                severity="Medium",
                evidence={
                    "rou_additions": rou_gross_additions,
                    "lease_liability": lease_liability_total,
                    "diff_pct": diff_pct,
                },
                note_ids=["5", "14b"],
                standard_query="right of use asset lease liability measurement Ind AS 116 commencement",
                triggered_by="ROU asset additions and new lease liabilities differ by >15%",
            )
    return None


def check_tax_reconciliation(
    current_tax: float | None, tax_prior: float | None, deferred_tax: float | None, total_tax_expense: float | None
) -> AuditFlag | None:
    """Sum of tax components should equal total tax expense."""
    if all(v is not None for v in [current_tax, tax_prior, deferred_tax, total_tax_expense]):
        computed = current_tax + tax_prior + deferred_tax
        if abs(computed - total_tax_expense) > 1:
            return AuditFlag(
                flag_id="TAX_RECONCILIATION_ERROR",
                area="Data Integrity – Tax Computation",
                severity="High",
                evidence={
                    "current_tax": current_tax, "tax_prior_years": tax_prior,
                    "deferred_tax": deferred_tax, "computed_total": computed,
                    "disclosed_total": total_tax_expense,
                    "difference": abs(computed - total_tax_expense),
                },
                note_ids=["28"],
                standard_query="income tax expense current deferred reconciliation Ind AS 12",
                triggered_by="Sum of current + prior year + deferred tax does not equal disclosed total",
            )
    return None


def _extract_cash_flow_text(full_text: str) -> str:
    match = re.search(r"cash\s+flow\s+statement", full_text, re.IGNORECASE)
    if not match:
        return ""
    return full_text[match.start(): match.start() + 3000]


def check_cashflow_column_header(full_text: str) -> AuditFlag | None:
    """Detect if the comparative column is mislabelled with the current year date."""
    cash_flow_section = _extract_cash_flow_text(full_text)
    dates_found = re.findall(r"31\s+March\s+(\d{4})", cash_flow_section[:500])
    if len(set(dates_found)) == 1 and len(dates_found) >= 2:
        return AuditFlag(
            flag_id="CASHFLOW_HEADER_ERROR",
            area="Data Presentation – Cash Flow Headers",
            severity="Low",
            evidence={"dates_found": dates_found},
            note_ids=[],
            standard_query="financial statement presentation comparative period disclosure Ind AS 1",
            triggered_by="Cash flow statement comparative column appears to have incorrect year label",
        )
    return None


def _find_restricted_balance(notes: dict[str, NoteSection]) -> float | None:
    """Best-effort search for a 'restricted' bank balance figure disclosed alongside cash notes."""
    from utils.text_utils import parse_indian_number

    for note in notes.values():
        for match in _RESTRICTED_BALANCE_PATTERN.finditer(note.raw_text):
            tail = note.raw_text[match.end(): match.end() + 40]
            num_match = _NUMBER_PATTERN.search(tail)
            if num_match:
                value = parse_indian_number(num_match.group(0))
                if value is not None:
                    return value
    return None


def _find_note_by_keywords(notes: dict[str, NoteSection], keywords: list[str]) -> NoteSection | None:
    for note in notes.values():
        haystack = (note.title + " " + note.raw_text[:200]).lower()
        if any(kw in haystack for kw in keywords):
            return note
    return None


def _find_row_value(note: NoteSection | None, label_keywords: list[str]) -> float | None:
    if note is None:
        return None
    for table in note.tables:
        for label, current, _prior, _note_ref in _parse_table_rows(table):
            if any(kw in label.lower() for kw in label_keywords):
                return current
    return None


def _extract_tax_components(
    notes: dict[str, NoteSection],
) -> tuple[float | None, float | None, float | None, float | None]:
    tax_note = _find_note_by_keywords(notes, ["income tax", "tax expense"])
    if tax_note is None:
        return None, None, None, None
    current_tax = _find_row_value(tax_note, ["current tax"])
    tax_prior = _find_row_value(tax_note, ["earlier year", "prior year", "earlier years"])
    deferred_tax = _find_row_value(tax_note, ["deferred tax"])
    total_tax_expense = _find_row_value(tax_note, ["total tax expense", "total income tax expense"])
    return current_tax, tax_prior, deferred_tax, total_tax_expense


def _extract_lease_rou_components(
    notes: dict[str, NoteSection],
) -> tuple[float | None, float | None]:
    rou_note = _find_note_by_keywords(notes, ["right-of-use", "right of use", "rou asset"])
    lease_note = _find_note_by_keywords(notes, ["lease liabilit"])
    rou_additions = _find_row_value(rou_note, ["addition"])
    lease_liability_total = _find_row_value(lease_note, ["total"])
    return lease_liability_total, rou_additions


def run_consistency_checks(
    structured_tables: StructuredTables, notes: dict[str, NoteSection], full_text: str
) -> list[AuditFlag]:
    """Run all cross-note consistency checks and collect every flag that triggers."""
    flags: list[AuditFlag] = []

    restricted_balance = _find_restricted_balance(notes)
    f = check_csr_balance_vs_bank(structured_tables.csr_details, restricted_balance)
    if f:
        flags.append(f)

    lease_liability_total, rou_additions = _extract_lease_rou_components(notes)
    f = check_lease_rou_consistency(lease_liability_total, rou_additions)
    if f:
        flags.append(f)

    current_tax, tax_prior, deferred_tax, total_tax_expense = _extract_tax_components(notes)
    f = check_tax_reconciliation(current_tax, tax_prior, deferred_tax, total_tax_expense)
    if f:
        flags.append(f)

    f = check_cashflow_column_header(full_text)
    if f:
        flags.append(f)

    f = check_multi_entity_document(full_text)
    if f:
        flags.append(f)

    logger.info(f"Consistency checks produced {len(flags)} flags")
    return flags
