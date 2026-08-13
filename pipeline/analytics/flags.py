"""Threshold-based flag generation. Every rule null-checks before arithmetic."""
from models.financial import (
    ContingentLiabilities,
    CSRDetails,
    CWIPAgeing,
    MappedLineItem,
    MovementRecord,
    MSMEDDisclosure,
    NoteSection,
    StructuredTables,
)
from models.flags import AuditFlag

# ── HIGH SEVERITY ──────────────────────────────────────────────────


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
        if m.prior is None and m.current and m.current > total_assets * 0.03:
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


def check_cfo_pat_divergence(cfo: float | None, pat: float | None) -> AuditFlag | None:
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
    if contingent and contingent.total is not None and contingent.total_prior is not None and total_equity:
        increase = contingent.total - contingent.total_prior
        if increase > total_equity * 0.05:
            return AuditFlag(
                flag_id="CONTINGENT_LIABILITY_JUMP",
                area="Contingent Liabilities",
                severity="High",
                evidence={
                    "current_total": contingent.total,
                    "prior_total": contingent.total_prior,
                    "increase": increase,
                    "income_tax": contingent.income_tax,
                    "gst": contingent.gst,
                    "service_tax": contingent.service_tax,
                },
                note_ids=["27"],
                standard_query="contingent liability disclosure provision Ind AS 37",
                triggered_by="Contingent liabilities increased by more than 5% of equity",
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


def check_msme_interest_accrued(msme_disclosure: MSMEDDisclosure | None) -> AuditFlag | None:
    if msme_disclosure and msme_disclosure.interest_accrued_unpaid and msme_disclosure.interest_accrued_unpaid > 0:
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
        check_cfo_pat_divergence(cfo, pat),
        check_rou_material_increase(movements, total_assets),
        check_contingent_liability_jump(structured_tables.contingent_liabilities, total_equity),
        check_other_financial_assets_surge(movements, total_assets),
    ]
    flags.extend(f for f in high_checks if f is not None)
    flags.extend(check_first_occurrence_material(movements, total_assets))

    medium_checks = [
        check_receivables_revenue_mismatch(movements),
        check_mse_payables_increase(movements, structured_tables.msmed_disclosure),
        check_prior_year_tax_recurring(tax_prior, current_tax),
        check_jv_profit_decline(movements),
        check_csr_unspent(structured_tables.csr_details),
        check_msme_interest_accrued(structured_tables.msmed_disclosure),
        check_trade_payables_sharp_reduction(movements),
    ]
    flags.extend(f for f in medium_checks if f is not None)

    low_checks = [
        check_actuarial_rate_change(structured_tables.actuarial_assumptions),
        check_cwip_overdue(structured_tables.cwip_ageing),
        check_unpaid_dividend(notes),
    ]
    flags.extend(f for f in low_checks if f is not None)

    return flags
