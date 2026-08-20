"""Deterministic, template-based narrative text for Layer 5.

Every one of Layer 4's ~21 flag types (pipeline/analytics/flags.py,
pipeline/analytics/consistency.py) already carries a fully structured
evidence dict and a governing standard baked into standard_query at the
point the check was written — there is no open-ended judgment left for a
model to make on the observation or recommendation text, only formatting.
Every number here is read straight out of flag.evidence, so unlike the old
LLM-generated path there is no paraphrase step and therefore no
transcription-error risk for these two fields.

build_templated_text() returns None for any flag_id without a template
(there shouldn't be one, given the registry below covers every check
function in flags.py/consistency.py, but if a new check is ever added
without a matching template, the caller falls back to the LLM path rather
than silently dropping the observation)."""
from models.flags import AuditFlag
from utils.text_utils import format_indian_number as fmt

_TEMPLATES: dict[str, "callable"] = {}


def _register(flag_id: str):
    def deco(fn):
        _TEMPLATES[flag_id] = fn
        return fn
    return deco


def _pct(x: float) -> str:
    return f"{x:.2f}"


def _money(x: float | None) -> str:
    return fmt(x, decimals=0) if x is not None else "N/A"


@_register("CASH_DECLINE")
def _cash_decline(flag: AuditFlag) -> tuple[str, str]:
    e = flag.evidence
    obs = (
        f"Cash and cash equivalents declined by {_pct(abs(e['pct_change']))}% from "
        f"₹{_money(e['prior'])} lakh to ₹{_money(e['current'])} lakh, a decline "
        "material to the company's total assets."
    )
    rec = (
        "Obtain a detailed explanation from management for the decline in cash "
        "and cash equivalents, and perform substantive testing of closing cash "
        "balances against bank statements and reconciliations."
    )
    return obs, rec


def _first_occurrence(flag: AuditFlag) -> tuple[str, str]:
    e = flag.evidence
    label = str(e.get("item", "")).replace("_", " ").title()
    obs = (
        f"{label} appears for the first time this year at ₹{_money(e['current'])} "
        f"lakh, representing {_pct(e['pct_of_assets'])}% of total assets — a new "
        "and material item not present in the prior year."
    )
    rec = (
        f"Obtain supporting documentation for the {label.lower()} recognized this "
        "year and confirm the basis of its recognition and measurement."
    )
    return obs, rec


@_register("CFO_PAT_DIVERGENCE")
def _cfo_pat_divergence(flag: AuditFlag) -> tuple[str, str]:
    e = flag.evidence
    ratio_pct = e["cfo_to_pat_ratio"] * 100
    obs = (
        f"Cash flow from operations of ₹{_money(e['cfo'])} lakh is only "
        f"{_pct(ratio_pct)}% of reported profit after tax of ₹{_money(e['pat'])} "
        "lakh, a material divergence between reported profitability and cash "
        "generation."
    )
    rec = (
        "Perform a detailed reconciliation between profit after tax and cash "
        "flow from operations, and obtain explanations for the working capital "
        "movements driving the divergence."
    )
    return obs, rec


@_register("ROU_MATERIAL_INCREASE")
def _rou_increase(flag: AuditFlag) -> tuple[str, str]:
    e = flag.evidence
    obs = (
        f"The right-of-use asset increased by {_pct(e['pct_change'])}% from "
        f"₹{_money(e['prior'])} lakh to ₹{_money(e['current'])} lakh, a movement "
        "material to total assets."
    )
    rec = (
        "Obtain the schedule of lease agreements and corresponding right-of-use "
        "assets to verify the increase, and confirm with management that it "
        "results from valid new lease transactions rather than errors or "
        "misstatements."
    )
    return obs, rec


@_register("CONTINGENT_LIABILITY_JUMP")
def _contingent_liability(flag: AuditFlag) -> tuple[str, str]:
    e = flag.evidence
    if e.get("increase") is not None:
        obs = (
            f"Contingent liabilities increased by ₹{_money(e['increase'])} lakh "
            f"to ₹{_money(e['current_total'])} lakh, representing "
            f"{_pct(e['exposure_pct_of_equity'])}% of total equity."
        )
    else:
        obs = (
            f"Contingent liabilities of ₹{_money(e['current_total'])} lakh "
            f"represent a significant {_pct(e['exposure_pct_of_equity'])}% "
            "exposure relative to total equity."
        )
    rec = (
        "Obtain a detailed schedule of all contingent liabilities, including "
        "the nature of the obligations and their potential financial effects, "
        "and confirm status with legal counsel or the relevant authorities "
        "where applicable."
    )
    return obs, rec


@_register("OTHER_FIN_ASSETS_SURGE")
def _other_fin_assets_surge(flag: AuditFlag) -> tuple[str, str]:
    e = flag.evidence
    obs = (
        f"Other financial assets (current) increased by {_pct(e['pct_change'])}% "
        f"from ₹{_money(e['prior'])} lakh to ₹{_money(e['current'])} lakh, a "
        "movement material to total assets."
    )
    rec = (
        "Obtain the breakup and supporting documentation for other financial "
        "assets, and assess the classification, measurement basis, and "
        "recoverability of the balance."
    )
    return obs, rec


@_register("NON_CURRENT_LIABILITIES_SURGE")
def _non_current_liabilities_surge(flag: AuditFlag) -> tuple[str, str]:
    e = flag.evidence
    obs = (
        f"Total non-current liabilities grew by {_pct(e['pct_change'])}% from "
        f"₹{_money(e['prior'])} lakh to ₹{_money(e['current'])} lakh, a movement "
        "material to total assets."
    )
    rec = (
        "Obtain the breakup of non-current liabilities and confirm appropriate "
        "classification and disclosure under Schedule III of the Companies Act."
    )
    return obs, rec


@_register("RECEIVABLES_REVENUE_MISMATCH")
def _receivables_revenue_mismatch(flag: AuditFlag) -> tuple[str, str]:
    e = flag.evidence
    rec_dir = "increased" if e["receivables_pct_change"] >= 0 else "declined"
    rev_dir = "increased" if e["revenue_pct_change"] >= 0 else "declined"
    obs = (
        f"Trade receivables {rec_dir} by {_pct(abs(e['receivables_pct_change']))}% "
        f"while revenue {rev_dir} by {_pct(abs(e['revenue_pct_change']))}% over "
        f"the same period — a divergence of {_pct(e['divergence'])} percentage "
        "points between the two."
    )
    rec = (
        "Obtain an aging schedule of trade receivables and assess the adequacy "
        "of the expected credit loss provision in light of the divergence from "
        "revenue growth."
    )
    return obs, rec


@_register("MSE_PAYABLES_INCREASE")
def _mse_payables_increase(flag: AuditFlag) -> tuple[str, str]:
    e = flag.evidence
    obs = (
        f"Trade payables to micro and small enterprises increased by "
        f"{_pct(e['pct_change'])}% from ₹{_money(e['prior'])} lakh to "
        f"₹{_money(e['current'])} lakh."
    )
    if e.get("interest_accrued"):
        obs += (
            f" Interest of ₹{_money(e['interest_accrued'])} lakh has also "
            "accrued on delayed payments to these enterprises."
        )
    rec = (
        "Obtain an aging schedule of MSE trade payables due for payment and "
        "confirm compliance with the 45-day payment requirement under the "
        "MSMED Act, 2006."
    )
    return obs, rec


@_register("PRIOR_YEAR_TAX_MATERIAL")
def _prior_year_tax(flag: AuditFlag) -> tuple[str, str]:
    e = flag.evidence
    ratio_pct = e["ratio"] * 100 if e.get("ratio") is not None else None
    obs = (
        f"Prior year tax adjustments of ₹{_money(e['tax_prior_years'])} lakh "
        + (f"represent {_pct(ratio_pct)}% of " if ratio_pct is not None else "are material relative to ")
        + f"the current year tax charge of ₹{_money(e['current_tax'])} lakh."
    )
    rec = (
        "Obtain detailed tax computations for the prior and current years and "
        "assess the reasons for the prior period adjustment."
    )
    return obs, rec


@_register("JV_PROFIT_DECLINE")
def _jv_profit_decline(flag: AuditFlag) -> tuple[str, str]:
    e = flag.evidence
    obs = (
        f"Share of profit from associates/joint ventures declined by "
        f"{_pct(abs(e['pct_change']))}% from ₹{_money(e['prior'])} lakh to "
        f"₹{_money(e['current'])} lakh."
    )
    rec = (
        "Obtain the latest financial statements of the associate/joint venture "
        "and assess whether an impairment indicator exists under Ind AS 28 or "
        "Ind AS 36."
    )
    return obs, rec


@_register("CSR_UNSPENT")
def _csr_unspent(flag: AuditFlag) -> tuple[str, str]:
    e = flag.evidence
    obs = (
        f"Of the CSR obligation of ₹{_money(e['obligation'])} lakh, "
        f"₹{_money(e['unspent'])} lakh remains unspent as at year end."
    )
    rec = (
        "Obtain the CSR expenditure schedule and confirm that the unspent "
        "amount has been transferred to the specified fund/escrow account "
        "within the statutory timeline under section 135 of the Companies "
        "Act, 2013."
    )
    return obs, rec


@_register("MSME_INTEREST_ACCRUED")
def _msme_interest_accrued(flag: AuditFlag) -> tuple[str, str]:
    e = flag.evidence
    obs = (
        f"Interest of ₹{_money(e['interest_accrued'])} lakh has accrued on "
        "delayed payments to micro and small enterprises."
    )
    rec = (
        "Obtain the MSME interest computation and confirm whether the interest "
        "is disallowable for income tax purposes under section 23 of the "
        "MSMED Act."
    )
    return obs, rec


@_register("TRADE_PAYABLES_REDUCTION")
def _trade_payables_reduction(flag: AuditFlag) -> tuple[str, str]:
    e = flag.evidence
    obs = (
        f"Trade payables (other than MSE) declined by {_pct(abs(e['pct_change']))}% "
        f"from ₹{_money(e['prior'])} lakh to ₹{_money(e['current'])} lakh while "
        "remaining material to total assets."
    )
    rec = (
        "Obtain an explanation for the sharp reduction in trade payables and "
        "verify whether it reflects early settlement, reclassification, or a "
        "data error."
    )
    return obs, rec


@_register("ACTUARIAL_RATE_CHANGE")
def _actuarial_rate_change(flag: AuditFlag) -> tuple[str, str]:
    e = flag.evidence
    obs = (
        "The actuarial discount rate used for defined benefit obligations "
        f"changed from {_pct(e['discount_rate_prior'])}% to "
        f"{_pct(e['discount_rate_current'])}%, a change of "
        f"{_pct(e['change_bps'])} basis points."
    )
    rec = (
        "Obtain the actuarial valuation report and confirm that the discount "
        "rate is consistent with market yields on government bonds of "
        "matching tenure, per Ind AS 19."
    )
    return obs, rec


@_register("CWIP_OVERDUE")
def _cwip_overdue(flag: AuditFlag) -> tuple[str, str]:
    projects = flag.evidence.get("overdue_projects", [])
    total = sum(p.get("amount", 0) for p in projects)
    names = [p.get("project_name") or "an unnamed project" for p in projects]
    listed = "; ".join(names[:3]) + (f" and {len(names) - 3} more" if len(names) > 3 else "")
    obs = (
        f"Capital work-in-progress includes {len(projects)} project(s) "
        f"outstanding for more than 3 years ({listed}), totaling "
        f"₹{_money(total)} lakh."
    )
    rec = (
        "Obtain the capitalization timeline for the overdue CWIP projects and "
        "assess whether they indicate a need for impairment or should be "
        "capitalized or written off."
    )
    return obs, rec


@_register("UNPAID_DIVIDEND")
def _unpaid_dividend(flag: AuditFlag) -> tuple[str, str]:
    obs = "The company has an unpaid dividend balance outstanding as at the year end."
    rec = (
        "Obtain the unpaid dividend account balance and confirm whether "
        "amounts remaining unclaimed for seven years have been transferred to "
        "the Investor Education and Protection Fund (IEPF) as required."
    )
    return obs, rec


@_register("XNOTE_CSR_BANK_MISMATCH")
def _xnote_csr_bank_mismatch(flag: AuditFlag) -> tuple[str, str]:
    e = flag.evidence
    obs = (
        f"The CSR unspent balance disclosed in the CSR note "
        f"(₹{_money(e['csr_note_unspent'])} lakh) does not match the "
        f"restricted bank balance disclosed elsewhere "
        f"(₹{_money(e['bank_note_restricted'])} lakh), a difference of "
        f"₹{_money(e['difference'])} lakh."
    )
    rec = (
        "Reconcile the CSR unspent balance with the restricted bank balance "
        "and correct the disclosure if the two are intended to represent the "
        "same amount."
    )
    return obs, rec


@_register("XNOTE_LEASE_ROU_MISMATCH")
def _xnote_lease_rou_mismatch(flag: AuditFlag) -> tuple[str, str]:
    e = flag.evidence
    obs = (
        f"New right-of-use asset additions (₹{_money(e['rou_additions'])} lakh) "
        f"and new lease liabilities recognized (₹{_money(e['lease_liability'])} "
        f"lakh) differ by {_pct(e['diff_pct'])}%."
    )
    rec = (
        "Obtain the lease-by-lease computation and reconcile the right-of-use "
        "asset additions with the corresponding lease liability recognized at "
        "commencement, per Ind AS 116."
    )
    return obs, rec


@_register("TAX_RECONCILIATION_ERROR")
def _tax_reconciliation_error(flag: AuditFlag) -> tuple[str, str]:
    e = flag.evidence
    obs = (
        "The sum of current tax, prior year tax, and deferred tax "
        f"(₹{_money(e['computed_total'])} lakh) does not equal the disclosed "
        f"total tax expense (₹{_money(e['disclosed_total'])} lakh), a "
        f"difference of ₹{_money(e['difference'])} lakh."
    )
    rec = (
        "Obtain the detailed tax computation and reconcile the components of "
        "tax expense with the amount disclosed in the statement of profit "
        "and loss."
    )
    return obs, rec


@_register("CASHFLOW_HEADER_ERROR")
def _cashflow_header_error(flag: AuditFlag) -> tuple[str, str]:
    dates = flag.evidence.get("dates_found", [])
    year = dates[0] if dates else "the current year"
    obs = (
        "The cash flow statement's comparative column appears to be labeled "
        f"with the same year ({year}) as the current period, suggesting a "
        "possible header error."
    )
    rec = (
        "Verify the column headers in the cash flow statement and confirm "
        "that the comparative period is correctly labeled as the prior year."
    )
    return obs, rec


@_register("AUDIT_TRAIL_LAPSE")
def _audit_trail_lapse(flag: AuditFlag) -> tuple[str, str]:
    excerpt = flag.evidence.get("excerpt", "")
    obs = (
        "The auditor's report indicates the accounting software's audit "
        "trail (edit log) feature was not continuously operational "
        "throughout the year, as required under Rule 11(g) of the "
        f"Companies (Audit and Auditors) Rules, 2014: “{excerpt}…”"
    )
    rec = (
        "Obtain the exact dates and cause of the audit trail gap, confirm "
        "whether any transactions during that period lack a traceable "
        "edit history, and verify the audit trail has since been restored "
        "and preserved per the statutory record-retention requirement."
    )
    return obs, rec


@_register("AUDITOR_EMPHASIS_PARAGRAPH")
def _auditor_emphasis_paragraph(flag: AuditFlag) -> tuple[str, str]:
    e = flag.evidence
    kind = e.get("paragraph_type", "Emphasis of Matter")
    excerpt = e.get("excerpt", "")
    obs = (
        f"The auditor's report contains a '{kind}' paragraph — a section "
        "only included when the auditor is drawing specific attention to "
        f"a matter of fundamental importance: “{excerpt}…” This is a "
        "qualitative disclosure, not a number, so it would not otherwise "
        "surface anywhere else in this report."
    )
    rec = (
        "Read the full paragraph in the auditor's report and assess "
        "whether the underlying matter changes how the rest of this "
        "filing's figures should be interpreted."
    )
    return obs, rec


@_register("MULTI_ENTITY_DOCUMENT")
def _multi_entity_document(flag: AuditFlag) -> tuple[str, str]:
    e = flag.evidence
    cins = e.get("distinct_cins", [])
    obs = (
        f"This document contains {e.get('count', len(cins))} distinct company "
        f"CINs (Corporate Identification Numbers): {', '.join(cins)}. This "
        "tool's analysis assumes a single reporting entity — figures from "
        "different companies or schemes bundled into one file may be "
        "silently mixed together in the results above, so every other "
        "observation in this report should be treated with reduced "
        "confidence until the document is split and each entity is "
        "analyzed separately."
    )
    rec = (
        "Split this document by entity (using the CINs above to identify "
        "boundaries) and re-run the analysis on each entity's financial "
        "statements individually for a reliable result."
    )
    return obs, rec


def _material_movement_generic(flag: AuditFlag) -> tuple[str, str]:
    e = flag.evidence
    label = e.get("display_label") or str(e.get("item", "")).replace("_", " ").title()
    direction = "increased" if (e.get("pct_change") or 0) > 0 else "decreased"
    obs = (
        f"{label} {direction} by {_pct(abs(e['pct_change']))}% year-over-year, "
        f"from ₹{_money(e['prior'])} lakh to ₹{_money(e['current'])} lakh — "
        f"above the {_pct(e['threshold_used'])}% threshold."
    )
    rec = (
        f"Obtain a detailed explanation from management for the movement in "
        f"{label.lower()} and corroborate it against supporting schedules "
        "before relying on the reported figure."
    )
    return obs, rec


def build_templated_text(flag: AuditFlag) -> tuple[str, str] | None:
    """(observation, recommendation) for a flag_id with a known template, or
    None if none exists — caller should fall back to the LLM path."""
    if flag.flag_id in _TEMPLATES:
        return _TEMPLATES[flag.flag_id](flag)
    if flag.flag_id.startswith("FIRST_OCCURRENCE_"):
        return _first_occurrence(flag)
    if flag.flag_id.startswith("MATERIAL_MOVEMENT_"):
        return _material_movement_generic(flag)
    return None
