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


# Rule 11(g) of the Companies (Audit and Auditors) Rules, 2014 (a separate
# requirement from CARO's 21 clauses, reported alongside them) requires
# auditors to state whether the accounting software's audit trail (edit
# log) was enabled, unmodified, and operated throughout the year. A clean
# filing states this compliance in one sentence with no exception; a real
# lapse layers an exception clause onto the same sentence — confirmed on a
# real filing: "...operated throughout the year...except for the period
# 1st April 2025 to 8th January 2026." A bare "audit trail" presence check
# would fire on nearly every filing since most simply confirm compliance,
# and a bare negation-word search is actively wrong here: the SAME
# compliant sentence also routinely says "has not been tampered with" —
# containing "not" while being good news, not a lapse. So this anchors
# negation specifically to enabled/operational/operat(ed/ing), not to
# "tampered", and separately treats "except" as its own strong signal.
_AUDIT_TRAIL_MENTION = re.compile(r"audit\s+trail", re.IGNORECASE)
_AUDIT_TRAIL_EXCEPTION = re.compile(
    r"\bexcept\b|not\s+(?:been\s+)?(?:enabled|operational|operat\w*)\b|"
    r"(?:did|does)\s+not\s+(?:have|maintain|use)\b|\bdisabled\b|\bdiscontinued\b",
    re.IGNORECASE,
)
_AUDIT_TRAIL_WINDOW = 400


def check_audit_trail_lapse(full_text: str) -> AuditFlag | None:
    """See module comment above _AUDIT_TRAIL_MENTION for the design
    rationale (why a bare keyword or negation search doesn't work here)."""
    for m in _AUDIT_TRAIL_MENTION.finditer(full_text):
        window = full_text[m.start(): m.start() + _AUDIT_TRAIL_WINDOW]
        if _AUDIT_TRAIL_EXCEPTION.search(window):
            snippet = re.sub(r"\s+", " ", window[:280]).strip()
            return AuditFlag(
                flag_id="AUDIT_TRAIL_LAPSE",
                area="Audit Trail Compliance (Rule 11(g))",
                severity="Medium",
                evidence={"excerpt": snippet},
                note_ids=[],
                standard_query="audit trail edit log accounting software Rule 11(g) Companies Audit and Auditors Rules",
                triggered_by="Auditor's report indicates the audit trail feature was not continuously operational",
            )
    return None


# "Emphasis of Matter" (SA 706) and "Material Uncertainty Related to Going
# Concern" (SA 570) are standard, NAMED section headings in an Indian/ISA-
# format auditor's report — unlike a bare "going concern" keyword (which
# appears in routine going-concern-basis-of-preparation boilerplate on
# every clean filing too), these headings only appear when the auditor is
# actually including a dedicated paragraph for something significant.
# Presence alone is the signal; no negation logic needed, unlike the
# audit-trail check above.
_MATERIAL_UNCERTAINTY_HEADING = re.compile(r"material\s+uncertainty\s+related\s+to\s+going\s+concern", re.IGNORECASE)
_EMPHASIS_OF_MATTER_HEADING = re.compile(r"emphasis\s+of\s+matter", re.IGNORECASE)

# "Qualified Opinion" (and its rarer, more severe siblings "Adverse Opinion"
# and "Disclaimer of Opinion") are standard, NAMED section headings that
# replace the routine "Opinion" heading only when the auditor is not giving
# a clean opinion -- confirmed absent entirely from three known-clean real
# filings (BPCL, KSDL, ONGC) and present as an exact heading in three
# known-qualified ones (SAIL, New India Assurance, CESCOM/Chamundeshwari
# Electricity Supply), each followed immediately by a "Basis for Qualified
# Opinion" paragraph naming the specific issue.
_QUALIFIED_OPINION_HEADING = re.compile(
    r"\b(qualified\s+opinion|adverse\s+opinion|disclaimer\s+of\s+opinion)\b", re.IGNORECASE
)


def check_qualified_opinion(full_text: str) -> AuditFlag | None:
    """See module comment above _QUALIFIED_OPINION_HEADING."""
    match = _QUALIFIED_OPINION_HEADING.search(full_text)
    if not match:
        return None
    excerpt = re.sub(r"\s+", " ", full_text[match.start(): match.start() + 500]).strip()
    return AuditFlag(
        flag_id="AUDITOR_QUALIFIED_OPINION",
        area="Auditor's Report — Opinion",
        severity="High",
        evidence={"opinion_type": match.group(1).title(), "excerpt": excerpt},
        note_ids=[],
        standard_query="qualified opinion adverse disclaimer basis for qualified opinion SA 705",
        triggered_by=f"Auditor's report contains a '{match.group(1).title()}' rather than an unqualified opinion",
    )


def check_emphasis_of_matter(full_text: str) -> AuditFlag | None:
    """See module comment above _MATERIAL_UNCERTAINTY_HEADING."""
    match = _MATERIAL_UNCERTAINTY_HEADING.search(full_text)
    kind = "Material Uncertainty Related to Going Concern"
    if not match:
        match = _EMPHASIS_OF_MATTER_HEADING.search(full_text)
        kind = "Emphasis of Matter"
    if not match:
        return None
    excerpt = re.sub(r"\s+", " ", full_text[match.start(): match.start() + 400]).strip()
    return AuditFlag(
        flag_id="AUDITOR_EMPHASIS_PARAGRAPH",
        area="Auditor's Report — Emphasis / Going Concern",
        severity="High" if kind.startswith("Material") else "Medium",
        evidence={"paragraph_type": kind, "excerpt": excerpt},
        note_ids=[],
        standard_query="emphasis of matter material uncertainty going concern SA 570 SA 706",
        triggered_by=f"Auditor's report contains a '{kind}' paragraph",
    )


# CAG (Comptroller and Auditor General) conducts a supplementary audit of
# every govt-company/CPSE filing under Section 143(6), and its comments are
# printed as a standalone section headed like the patterns below. Nearly
# every CPSE auditor's report routinely cites CARO 2020 / Section 143(5)
# CAG-directions reporting as boilerplate (it's a mandatory disclosure, not
# a risk signal by itself) -- the real signal is when CAG's OWN
# supplementary review calls out that this reporting was deficient.
# Confirmed against a real filing (ONGC FY24-25): CAG's comments state the
# "report given by the Statutory Auditors... is incorrect... and is in
# non-compliance of reporting requirements of CARO 2020" after finding an
# undisclosed title-deed issue -- this is CAG faulting the auditor's own
# report, not the company's numbers, which none of the existing checks
# capture.
_CAG_COMMENTS_SECTION = re.compile(
    r"comments?\s+of\s+the\s+comptroller\s+and\s+auditor\s+general.{0,80}"
    r"(?:section\s*143\s*\(?\s*6\s*\)?|supplementary\s+audit)",
    re.IGNORECASE | re.DOTALL,
)
_CAG_REPORTING_DEFICIENCY = re.compile(
    r"\bincorrect\b|non[- ]compliance|\bnot\s+(?:recorded|disclosed|reported)\b|"
    r"was\s+not\s+(?:recorded|disclosed|reported|included)",
    re.IGNORECASE,
)
_CAG_DEFICIENCY_WINDOW = 10000


def check_cag_auditor_reporting_deficiency(full_text: str) -> AuditFlag | None:
    """See module comment above _CAG_COMMENTS_SECTION."""
    for m in _CAG_COMMENTS_SECTION.finditer(full_text):
        window = full_text[m.end(): m.end() + _CAG_DEFICIENCY_WINDOW]
        deficiency = _CAG_REPORTING_DEFICIENCY.search(window)
        if deficiency:
            snippet = re.sub(r"\s+", " ", window[max(0, deficiency.start() - 200): deficiency.start() + 200]).strip()
            return AuditFlag(
                flag_id="CAG_AUDITOR_REPORTING_DEFICIENCY",
                area="Statutory Auditor's Report — CAG Supplementary Review",
                severity="High",
                evidence={"excerpt": snippet},
                note_ids=[],
                standard_query="CAG supplementary audit section 143(6) CARO 2020 auditor reporting deficiency",
                triggered_by="CAG's supplementary audit comments indicate the statutory auditor's own report (CARO 2020 / Section 143(5) directions) was deficient or non-compliant",
            )
    return None


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

    f = check_audit_trail_lapse(full_text)
    if f:
        flags.append(f)

    f = check_cag_auditor_reporting_deficiency(full_text)
    if f:
        flags.append(f)

    f = check_qualified_opinion(full_text)
    if f:
        flags.append(f)

    f = check_emphasis_of_matter(full_text)
    if f:
        flags.append(f)

    logger.info(f"Consistency checks produced {len(flags)} flags")
    return flags
