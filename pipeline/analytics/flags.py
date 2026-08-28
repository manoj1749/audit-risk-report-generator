"""Threshold-based flag generation. Every rule null-checks before arithmetic."""
import re

from models.financial import (
    ContingentLiabilities,
    CSRDetails,
    CWIPAgeing,
    MappedLineItem,
    MovementRecord,
    MSMEDDisclosure,
    NoteSection,
    PPEDepreciationRollforward,
    StructuredTables,
    TradeReceivablesECLSummary,
)
from models.flags import AuditFlag

# ── HIGH SEVERITY ──────────────────────────────────────────────────


def check_negative_net_worth(movements: dict[str, MovementRecord]) -> AuditFlag | None:
    """Total equity < 0 is a going-concern-level fact on its own -- confirmed
    on a real filing (CESCOM/Chamundeshwari Electricity Supply) where net
    worth was ₹(2,798.51) crore, worsening 53% YoY, and the tool had no way
    to surface this at all despite total_equity already being extracted."""
    m = movements.get("total_equity")
    if m and m.current is not None and m.current < 0:
        return AuditFlag(
            flag_id="NEGATIVE_NET_WORTH",
            area="Going Concern — Net Worth",
            severity="High",
            evidence={
                "current": m.current, "prior": m.prior,
                "pct_change": m.pct_change,
            },
            note_ids=[],
            standard_query="going concern negative net worth erosion Ind AS 1 SA 570",
            triggered_by="Total equity (net worth) is negative",
        )
    return None


def check_cash_decline(movements: dict[str, MovementRecord], total_assets: float | None) -> AuditFlag | None:
    m = movements.get("cash_equivalents")
    if m and m.pct_change is not None and m.pct_change < -40 and m.materiality_pct is not None and m.materiality_pct > 0.5:
        return AuditFlag(
            flag_id="CASH_DECLINE",
            area="Liquidity & Cash Flow",
            severity="High",
            evidence={
                "current": m.current, "prior": m.prior,
                "pct_change": m.pct_change, "absolute_change": m.absolute_change,
            },
            note_ids=["7f", "14a", "14d"],
            standard_query="going concern liquidity cash flow assessment Ind AS 1",
            triggered_by="Cash and cash equivalents declined >40% and material to assets",
        )
    return None


def check_first_occurrence_material(movements: dict[str, MovementRecord], total_assets: float | None) -> list[AuditFlag]:
    flags = []
    if not total_assets:
        return flags
    for key, m in movements.items():
        if m.prior is None and not m.prior_suppressed and m.current and m.current > total_assets * 0.03:
            flags.append(AuditFlag(
                flag_id=f"FIRST_OCCURRENCE_{key.upper()}",
                area="New Significant Item",
                severity="High",
                evidence={
                    "item": key, "current": m.current,
                    "pct_of_assets": m.current / total_assets * 100,
                },
                note_ids=[],
                standard_query="first-time recognition disclosure Ind AS",
                triggered_by=f"{key} appears for first time and exceeds 3% of total assets",
            ))
    return flags


# For a lending institution (NBFC/HFC), loan disbursements to customers are
# classified as an OPERATING cash outflow under Ind AS 7, so a growing loan
# book structurally produces deeply negative operating cash flow every
# year regardless of profitability -- not an earnings-quality problem the
# way it is for a non-lender. Confirmed false positive on a real filing
# (BOBCARD, a credit-card NBFC): CFO was deeply negative both years
# (-12,527 / -18,409) purely from loan-book growth while PAT was solidly
# positive both years -- textbook business-as-usual for a lender, not
# divergence worth flagging.
# Confirmed real self-description wording uses "Finance", not "Financial"
# (BOBCARD: "...a Non-Deposit accepting Systemically Important Non-Banking
# Finance Company ('NBFC-ND-SI'), holding a Certificate of Registration
# from..."), so the full-phrase branch needs to match both spellings.
_LENDING_INSTITUTION_MARKERS = re.compile(
    r"non-?banking\s+financ(?:e|ial)\s+compan\w*|\bnbfc\b|housing\s+finance\s+compan\w*",
    re.IGNORECASE,
)
# A bare "NBFC" is also the standard way every company's MSMED/borrowings
# note enumerates its LOAN SOURCES ("Long term loans - banks/NBFC/others"),
# not a self-description -- confirmed false positive on a real filing
# (Chamundeshwari Electricity Supply, a discom, not a lender): the only
# "NBFC" hits in the whole document are two of these loan-category table
# rows. A genuine self-description never lists NBFC as a bare alternative
# immediately next to "bank(s)" like this.
_NBFC_AS_LENDER_CATEGORY = re.compile(r"banks?[\s,/]{0,3}nbfc|nbfc[\s,/]{0,3}(?:banks?|others?)", re.IGNORECASE)


def _is_lending_institution(full_text: str) -> bool:
    for m in _LENDING_INSTITUTION_MARKERS.finditer(full_text):
        window = full_text[max(0, m.start() - 30): m.end() + 30]
        if _NBFC_AS_LENDER_CATEGORY.search(window):
            continue
        return True
    return False


def check_cfo_pat_divergence(cfo: float | None, pat: float | None, full_text: str = "") -> AuditFlag | None:
    if _is_lending_institution(full_text):
        return None
    if pat and pat > 0 and cfo is not None and cfo < pat * 0.6:
        return AuditFlag(
            flag_id="CFO_PAT_DIVERGENCE",
            area="Cash Flow Quality",
            severity="High",
            evidence={"cfo": cfo, "pat": pat, "cfo_to_pat_ratio": cfo / pat},
            note_ids=[],
            standard_query="cash flow from operations profit reconciliation working capital Ind AS 7",
            triggered_by="Operating cash flow is less than 60% of reported profit after tax",
        )
    return None


def check_cashflow_reconciliation(movements: dict[str, MovementRecord]) -> AuditFlag | None:
    opening = movements.get("opening_cash")
    cfo = movements.get("cfo")
    cfi = movements.get("cfi")
    cff = movements.get("cff")
    closing = movements.get("closing_cash")
    if not all(m is not None and m.current is not None for m in [opening, cfo, cfi, cff, closing]):
        return None
    expected_closing = opening.current + cfo.current + cfi.current + cff.current
    diff = abs(expected_closing - closing.current)
    if diff <= 1:
        return None
    return AuditFlag(
        flag_id="CASHFLOW_RECONCILIATION_ERROR",
        area="Data Integrity — Cash Flow Statement",
        severity="High",
        evidence={
            "opening_cash": opening.current,
            "cfo": cfo.current,
            "cfi": cfi.current,
            "cff": cff.current,
            "computed_closing": expected_closing,
            "disclosed_closing": closing.current,
            "difference": diff,
        },
        note_ids=[],
        standard_query="cash flow statement reconciliation opening closing balance Ind AS 7",
        triggered_by="Opening cash plus CFO, CFI and CFF does not reconcile to the disclosed closing cash balance",
    )


def check_ppe_depreciation_reconciliation(ppe: PPEDepreciationRollforward | None) -> AuditFlag | None:
    if ppe is None:
        return None
    fields = [ppe.opening_accumulated_depreciation, ppe.depreciation_charge, ppe.disposals, ppe.closing_accumulated_depreciation]
    if any(f is None for f in fields):
        return None
    expected_closing = ppe.opening_accumulated_depreciation + ppe.depreciation_charge + ppe.disposals
    diff = abs(expected_closing - ppe.closing_accumulated_depreciation)
    if diff <= 1:
        return None
    return AuditFlag(
        flag_id="PPE_DEPRECIATION_RECONCILIATION_ERROR",
        area="Data Integrity — Property, Plant & Equipment",
        severity="Medium",
        evidence={
            "opening_accumulated_depreciation": ppe.opening_accumulated_depreciation,
            "depreciation_charge": ppe.depreciation_charge,
            "disposals": ppe.disposals,
            "computed_closing": expected_closing,
            "disclosed_closing": ppe.closing_accumulated_depreciation,
            "difference": diff,
        },
        note_ids=[],
        standard_query="property plant equipment depreciation roll forward reconciliation Ind AS 16 Schedule II",
        triggered_by="Opening accumulated depreciation plus the current-year charge, net of disposals, does not reconcile to the disclosed closing accumulated depreciation",
    )


def check_rou_material_increase(movements: dict[str, MovementRecord], total_assets: float | None) -> AuditFlag | None:
    m = movements.get("roa_net")
    if m and m.pct_change is not None and m.pct_change > 100 and m.materiality_pct is not None and m.materiality_pct > 2:
        return AuditFlag(
            flag_id="ROU_MATERIAL_INCREASE",
            area="Right-of-Use Assets & Lease Liabilities",
            severity="High",
            evidence={"current": m.current, "prior": m.prior, "pct_change": m.pct_change},
            note_ids=["5", "14b", "33"],
            standard_query="lease recognition measurement lessee right-of-use asset Ind AS 116",
            triggered_by="ROU assets increased >100% and exceed 2% of total assets",
        )
    return None


def check_contingent_liability_jump(
    contingent: ContingentLiabilities | None, total_equity: float | None
) -> AuditFlag | None:
    """Fires on either a material YoY increase, or a large absolute exposure —
    a contingent liability total that's already a big fraction of equity is a
    real risk worth flagging even in a year where it didn't move much further."""
    if contingent and contingent.total is not None and total_equity:
        increase = (
            contingent.total - contingent.total_prior
            if contingent.total_prior is not None else None
        )
        exposure_pct = contingent.total / total_equity * 100
        increase_material = increase is not None and increase > total_equity * 0.05
        exposure_material = exposure_pct > 25
        if increase_material or exposure_material:
            return AuditFlag(
                flag_id="CONTINGENT_LIABILITY_JUMP",
                area="Contingent Liabilities",
                severity="High",
                evidence={
                    "current_total": contingent.total,
                    "prior_total": contingent.total_prior,
                    "increase": increase,
                    "exposure_pct_of_equity": exposure_pct,
                    "income_tax": contingent.income_tax,
                    "gst": contingent.gst,
                    "service_tax": contingent.service_tax,
                },
                note_ids=["27"],
                standard_query="contingent liability disclosure provision Ind AS 37",
                triggered_by=(
                    "Contingent liabilities increased by more than 5% of equity"
                    if increase_material else
                    "Contingent liabilities are a large absolute exposure relative to equity (>25%)"
                ),
            )
    return None


def check_other_financial_assets_surge(movements: dict[str, MovementRecord], total_assets: float | None) -> AuditFlag | None:
    m = movements.get("other_financial_assets_current")
    if m and m.pct_change is not None and m.pct_change > 80 and m.materiality_pct is not None and m.materiality_pct > 5:
        return AuditFlag(
            flag_id="OTHER_FIN_ASSETS_SURGE",
            area="Other Financial Assets (Current)",
            severity="High",
            evidence={"current": m.current, "prior": m.prior, "pct_change": m.pct_change},
            note_ids=["7c"],
            standard_query="other financial assets classification measurement recoverability Ind AS 109",
            triggered_by="Other current financial assets surged >80% and exceed 5% of total assets",
        )
    return None


# ── MEDIUM SEVERITY ────────────────────────────────────────────────


def check_non_current_liabilities_surge(
    movements: dict[str, MovementRecord], total_assets: float | None
) -> AuditFlag | None:
    m = movements.get("total_non_current_liabilities")
    if m and m.pct_change is not None and m.pct_change > 50 and m.materiality_pct is not None and m.materiality_pct > 5:
        return AuditFlag(
            flag_id="NON_CURRENT_LIABILITIES_SURGE",
            area="Non-Current Liabilities",
            severity="Medium",
            evidence={"current": m.current, "prior": m.prior, "pct_change": m.pct_change},
            note_ids=[],
            standard_query="non-current liabilities recognition disclosure Schedule III presentation",
            triggered_by="Total non-current liabilities grew >50% and exceed 5% of total assets",
        )
    return None


def check_receivables_revenue_mismatch(movements: dict[str, MovementRecord]) -> AuditFlag | None:
    rec = movements.get("trade_receivables")
    rev = movements.get("revenue_from_operations")
    if rec and rev and rec.pct_change is not None and rev.pct_change is not None:
        divergence = abs(rec.pct_change - rev.pct_change)
        if divergence > 20:
            return AuditFlag(
                flag_id="RECEIVABLES_REVENUE_MISMATCH",
                area="Trade Receivables & ECL Provisioning",
                severity="Medium",
                evidence={
                    "receivables_pct_change": rec.pct_change,
                    "revenue_pct_change": rev.pct_change,
                    "receivables_current": rec.current,
                    "revenue_current": rev.current,
                    "divergence": divergence,
                },
                note_ids=["7e", "37"],
                standard_query="expected credit loss trade receivables provision matrix Ind AS 109",
                triggered_by="Trade receivables and revenue growth diverge by >20 percentage points",
            )
    return None


def check_mse_payables_increase(
    movements: dict[str, MovementRecord], msme_disclosure: MSMEDDisclosure | None
) -> AuditFlag | None:
    m = movements.get("trade_payables_mse")
    if m and m.pct_change is not None and m.pct_change > 40:
        return AuditFlag(
            flag_id="MSE_PAYABLES_INCREASE",
            area="Trade Payables – MSME Compliance",
            severity="Medium",
            evidence={
                "current": m.current, "prior": m.prior,
                "pct_change": m.pct_change,
                "interest_accrued": msme_disclosure.interest_accrued_unpaid if msme_disclosure else None,
            },
            note_ids=["14e"],
            standard_query="MSMED Act 2006 section 15 16 payment 45 days interest",
            triggered_by="MSE trade payables increased >40% and MSMED Act compliance at risk",
        )
    return None


def check_prior_year_tax_recurring(tax_prior: float | None, current_tax: float | None) -> AuditFlag | None:
    if tax_prior and current_tax and abs(tax_prior) > abs(current_tax) * 0.4:
        return AuditFlag(
            flag_id="PRIOR_YEAR_TAX_MATERIAL",
            area="Income Tax – Prior Period Adjustments",
            severity="Medium",
            evidence={
                "tax_prior_years": tax_prior,
                "current_tax": current_tax,
                "ratio": abs(tax_prior) / abs(current_tax) if current_tax else None,
            },
            note_ids=["28", "41"],
            standard_query="prior period tax adjustment earlier years income tax Ind AS 12",
            triggered_by="Prior year tax adjustments exceed 40% of current year tax charge",
        )
    return None


def check_jv_profit_decline(movements: dict[str, MovementRecord]) -> AuditFlag | None:
    m = movements.get("share_of_jv_profit")
    if m and m.pct_change is not None and m.pct_change < -25:
        return AuditFlag(
            flag_id="JV_PROFIT_DECLINE",
            area="Investments in Associates / Joint Ventures",
            severity="Medium",
            evidence={"current": m.current, "prior": m.prior, "pct_change": m.pct_change},
            note_ids=["6a", "51"],
            standard_query="equity method impairment associate joint venture Ind AS 28 Ind AS 36",
            triggered_by="Share of JV/associate profit declined >25%",
        )
    return None


def check_csr_unspent(csr_details: CSRDetails | None) -> AuditFlag | None:
    if csr_details and csr_details.amount_unspent and csr_details.amount_unspent > 0:
        return AuditFlag(
            flag_id="CSR_UNSPENT",
            area="CSR Compliance",
            severity="Medium",
            evidence={
                "obligation": csr_details.obligation,
                "spent": csr_details.amount_spent,
                "unspent": csr_details.amount_unspent,
            },
            note_ids=["25b"],
            standard_query="CSR unspent section 135 Companies Act 2013 transfer escrow",
            triggered_by="CSR obligation not fully spent during the year",
        )
    return None


_MSME_INTEREST_IMPLAUSIBLE_FRACTION_OF_ASSETS = 0.05


def check_msme_interest_accrued(
    msme_disclosure: MSMEDDisclosure | None, total_assets: float | None = None
) -> AuditFlag | None:
    if msme_disclosure and msme_disclosure.interest_accrued_unpaid and msme_disclosure.interest_accrued_unpaid > 0:
        # Interest accrued on delayed MSME payments should be a small fraction
        # of the balance sheet -- confirmed a real case where this figure
        # came back multiple orders of magnitude too large (a wrong-column
        # extraction), which this guard now suppresses rather than reporting
        # a nonsensical figure as fact.
        if total_assets and msme_disclosure.interest_accrued_unpaid > total_assets * _MSME_INTEREST_IMPLAUSIBLE_FRACTION_OF_ASSETS:
            return None
        return AuditFlag(
            flag_id="MSME_INTEREST_ACCRUED",
            area="MSME Interest Liability",
            severity="Medium",
            evidence={
                "interest_accrued": msme_disclosure.interest_accrued_unpaid,
                "interest_further_due": msme_disclosure.interest_further_due,
            },
            note_ids=["14e"],
            standard_query="MSMED Act interest delayed payment section 16 section 23 disallowance",
            triggered_by="Interest accrued on delayed MSME payments is non-zero",
        )
    return None


def check_trade_payables_sharp_reduction(movements: dict[str, MovementRecord]) -> AuditFlag | None:
    m = movements.get("trade_payables_others")
    if m and m.pct_change is not None and m.pct_change < -25 and m.materiality_pct is not None and m.materiality_pct > 3:
        return AuditFlag(
            flag_id="TRADE_PAYABLES_REDUCTION",
            area="Trade Payables",
            severity="Medium",
            evidence={"current": m.current, "prior": m.prior, "pct_change": m.pct_change},
            note_ids=["14e"],
            standard_query="trade payables reduction early settlement reclassification Ind AS 32",
            triggered_by="Trade payables (non-MSE) declined sharply while remaining material",
        )
    return None


# ── LOW SEVERITY ───────────────────────────────────────────────────


def check_actuarial_rate_change(actuarial) -> AuditFlag | None:
    if actuarial and actuarial.discount_rate_current is not None and actuarial.discount_rate_prior is not None:
        change = abs(actuarial.discount_rate_current - actuarial.discount_rate_prior)
        if change > 0.3:
            return AuditFlag(
                flag_id="ACTUARIAL_RATE_CHANGE",
                area="Defined Benefit Obligations – Actuarial",
                severity="Low",
                evidence={
                    "discount_rate_current": actuarial.discount_rate_current,
                    "discount_rate_prior": actuarial.discount_rate_prior,
                    "change_bps": change * 100,
                },
                note_ids=["30"],
                standard_query="actuarial assumptions discount rate defined benefit Ind AS 19",
                triggered_by="Actuarial discount rate changed by more than 30 bps",
            )
    return None


_ECL_ALLOWANCE_GROWTH_THRESHOLD = 20.0


def check_ecl_allowance_surge(ecl: TradeReceivablesECLSummary | None) -> AuditFlag | None:
    if ecl is None or ecl.allowance_current is None or ecl.allowance_prior in (None, 0):
        return None
    pct_change = (ecl.allowance_current - ecl.allowance_prior) / abs(ecl.allowance_prior) * 100
    if pct_change < _ECL_ALLOWANCE_GROWTH_THRESHOLD:
        return None
    return AuditFlag(
        flag_id="ECL_ALLOWANCE_SURGE",
        area="Trade Receivables & ECL Provisioning",
        severity="High" if pct_change >= 50 else "Medium",
        evidence={
            "allowance_current": ecl.allowance_current,
            "allowance_prior": ecl.allowance_prior,
            "pct_change": pct_change,
            "gross_current": ecl.gross_current,
            "gross_prior": ecl.gross_prior,
        },
        note_ids=[],
        standard_query="expected credit loss allowance doubtful debts trade receivables Ind AS 109",
        triggered_by=f"Allowance for doubtful debts/expected credit loss grew {pct_change:.1f}% year-over-year",
    )


def check_cwip_overdue(cwip_ageing: CWIPAgeing | None) -> AuditFlag | None:
    if cwip_ageing:
        overdue = [p for p in cwip_ageing.projects if p.age_bucket == "more_than_3_years" and p.amount > 0]
        if overdue:
            return AuditFlag(
                flag_id="CWIP_OVERDUE",
                area="Capital Work-in-Progress",
                severity="Low",
                evidence={"overdue_projects": [p.model_dump() for p in overdue]},
                note_ids=["4"],
                standard_query="capital work in progress ageing impairment capitalization schedule III",
                triggered_by="CWIP items outstanding for more than 3 years",
            )
    return None


# ── GENERIC ────────────────────────────────────────────────────────

# Canonical keys already covered by a dedicated rule above -- skip them here
# so the same underlying movement isn't flagged twice under two different
# rubrics (e.g. cash_equivalents is CASH_DECLINE's job, not this rule's).
_MATERIAL_MOVEMENT_EXCLUDED_KEYS = {
    "cash_equivalents", "roa_net", "other_financial_assets_current",
    "total_non_current_liabilities", "trade_receivables", "revenue_from_operations",
    "trade_payables_mse", "trade_payables_others", "share_of_jv_profit",
    "tax_prior_years", "current_tax", "cfo", "pat",
    # opening_cash, closing_cash and net_cash_change are arithmetically
    # downstream of cfo + cfi + cff (opening + cfo + cfi + cff = closing;
    # net_cash_change = cfo + cfi + cff -- see check_cashflow_reconciliation
    # above), not independent risk signals -- confirmed redundant on 3 real
    # filings (stockholding-services 6 of 9 flags, BPCL 11 of 17, BOBCARD 3
    # of 7): one financing/investing event (a rights issue, an equity
    # raise) mechanically moves all three together with cfi/cff, and each
    # got counted as its own separate observation. cfi and cff stay
    # included -- investing vs. financing activity are genuinely distinct
    # economic events, each worth its own flag; it's specifically their
    # downstream arithmetic consequences that shouldn't also fire.
    "opening_cash", "closing_cash", "net_cash_change",
}

_MATERIAL_MOVEMENT_THRESHOLD = 20.0


def check_material_movement_generic(
    movements: dict[str, MovementRecord], total_assets: float | None
) -> list[AuditFlag]:
    """Any mapped line item (not already covered by a dedicated rule above)
    that moves more than 20% year-over-year, in either direction.

    Thresholds and severity bands are from Monali directly, in two passes:
    her first voice note proposed a size-adjusted base threshold (15%
    smaller company / 20% bigger), but when the Excel draft came back
    proposing "flag everything, just at Low severity below 30%" she
    rejected that ("All won't make sense") and simplified to a single flat
    rule: "Only above 20%" -- no company-size adjustment, nothing below the
    threshold gets flagged at all. Severity tiers (20-50% Low, 50-80%
    Medium, >80% High) are unchanged from what she confirmed earlier. A
    materiality floor (>0.5% of total assets, the same floor CASH_DECLINE
    uses) keeps this from flagging trivial rupee-amount swings that happen
    to have a big percentage change."""
    flags: list[AuditFlag] = []
    if not total_assets:
        return flags

    for key, m in movements.items():
        if key in _MATERIAL_MOVEMENT_EXCLUDED_KEYS or key.startswith("total_"):
            continue
        if m.pct_change is None or m.materiality_pct is None or m.materiality_pct <= 0.5:
            continue
        magnitude = abs(m.pct_change)
        if magnitude < _MATERIAL_MOVEMENT_THRESHOLD:
            continue
        severity = "High" if magnitude >= 80 else "Medium" if magnitude >= 50 else "Low"
        flags.append(AuditFlag(
            flag_id=f"MATERIAL_MOVEMENT_{key.upper()}",
            area="Material Line Item Movement",
            severity=severity,
            evidence={
                "item": key, "display_label": m.display_label,
                "current": m.current, "prior": m.prior,
                "pct_change": m.pct_change, "threshold_used": _MATERIAL_MOVEMENT_THRESHOLD,
            },
            note_ids=[m.note_ref] if m.note_ref else [],
            standard_query="analytical procedures significant fluctuations unusual variance SA 520",
            triggered_by=(
                f"{key} moved {magnitude:.1f}% year-over-year, exceeding the "
                f"{_MATERIAL_MOVEMENT_THRESHOLD:.0f}% threshold"
            ),
        ))
    return flags


def check_unpaid_dividend(notes: dict[str, NoteSection]) -> AuditFlag | None:
    note_7f = notes.get("7f")
    text = note_7f.raw_text.lower() if note_7f else ""
    if "unpaid dividend" in text:
        return AuditFlag(
            flag_id="UNPAID_DIVIDEND",
            area="Unpaid Dividend – IEPF",
            severity="Low",
            evidence={},
            note_ids=["7f"],
            standard_query="unpaid dividend IEPF investor education protection fund Companies Act 205C",
            triggered_by="Unpaid dividend balance exists — check IEPF transfer compliance",
        )
    return None


def generate_all_flags(
    movements: dict[str, MovementRecord],
    mapped_items: dict[str, MappedLineItem],
    structured_tables: StructuredTables,
    notes: dict[str, NoteSection],
    full_text: str = "",
) -> list[AuditFlag]:
    """Apply every flag rule and collect every flag that triggers."""
    flags: list[AuditFlag] = []

    def _val(key: str) -> float | None:
        m = movements.get(key)
        return m.current if m else None

    total_assets = _val("total_assets")
    total_equity = _val("total_equity")
    cfo = _val("cfo")
    pat = _val("pat")
    tax_prior = _val("tax_prior_years")
    current_tax = _val("current_tax")

    high_checks = [
        check_cash_decline(movements, total_assets),
        check_negative_net_worth(movements),
        check_cfo_pat_divergence(cfo, pat, full_text),
        check_rou_material_increase(movements, total_assets),
        check_contingent_liability_jump(structured_tables.contingent_liabilities, total_equity),
        check_other_financial_assets_surge(movements, total_assets),
        check_cashflow_reconciliation(movements),
    ]
    flags.extend(f for f in high_checks if f is not None)
    flags.extend(check_first_occurrence_material(movements, total_assets))

    medium_checks = [
        check_non_current_liabilities_surge(movements, total_assets),
        check_receivables_revenue_mismatch(movements),
        check_mse_payables_increase(movements, structured_tables.msmed_disclosure),
        check_prior_year_tax_recurring(tax_prior, current_tax),
        check_jv_profit_decline(movements),
        check_csr_unspent(structured_tables.csr_details),
        check_msme_interest_accrued(structured_tables.msmed_disclosure, total_assets),
        check_trade_payables_sharp_reduction(movements),
        check_ppe_depreciation_reconciliation(structured_tables.ppe_depreciation),
        check_ecl_allowance_surge(structured_tables.trade_receivables_ecl),
    ]
    flags.extend(f for f in medium_checks if f is not None)

    low_checks = [
        check_actuarial_rate_change(structured_tables.actuarial_assumptions),
        check_cwip_overdue(structured_tables.cwip_ageing),
        check_unpaid_dividend(notes),
    ]
    flags.extend(f for f in low_checks if f is not None)

    flags.extend(check_material_movement_generic(movements, total_assets))

    return flags
