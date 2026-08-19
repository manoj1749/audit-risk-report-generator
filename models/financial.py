"""Pydantic models for extracted, segmented, and normalized financial data.

These models are the contract between the extraction (Layer 1), segmentation
(Layer 2), normalization (Layer 3), and analytics (Layer 4) pipeline layers.
"""
from pydantic import BaseModel, Field


# ── Layer 1: Extraction ──────────────────────────────────────────────

class TableData(BaseModel):
    headers: list[str]
    rows: list[list[str | float | None]]  # PDF tables are str; Excel-sourced tables carry native floats
    page_num: int


class PageContent(BaseModel):
    page_num: int
    raw_text: str
    tables: list[TableData] = Field(default_factory=list)
    ocr: bool = False


class ExtractedDocument(BaseModel):
    pages: list[PageContent]
    full_text: str
    extraction_method: str  # 'pdfplumber' or 'paddleocr'
    company_name: str | None = None
    period: str | None = None
    total_pages: int


class CellComment(BaseModel):
    sheet: str
    cell_ref: str
    value: str | float | None
    comment_text: str
    comment_author: str | None = None


class SheetData(BaseModel):
    sheet_name: str
    sheet_type: str  # 'balance_sheet', 'pnl', 'cash_flow', 'unknown'
    headers: list[str]
    rows: list[list[str | float | None]]
    comments: list[CellComment] = Field(default_factory=list)


class ExtractedWorkbook(BaseModel):
    sheets: list[SheetData]
    all_comments: list[CellComment] = Field(default_factory=list)


# ── Layer 2: Note Segmentation ───────────────────────────────────────

class NoteSection(BaseModel):
    note_id: str            # e.g. "7", "6a", "14b"
    full_id: str             # e.g. "Note 7", "Note 6(a)"
    title: str
    raw_text: str
    tables: list[TableData] = Field(default_factory=list)
    page_start: int
    page_end: int
    referenced_by: list[str] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)


# ── Layer 3: Normalization ───────────────────────────────────────────

class MappedLineItem(BaseModel):
    raw_label: str
    canonical_key: str | None
    current_value: float | None
    prior_value: float | None
    confidence: float
    method: str  # 'exact', 'fuzzy', 'embedding', 'unknown'
    note_ref: str | None = None


class TradeReceivablesAgeing(BaseModel):
    not_due: float | None = None
    zero_to_six_months: float | None = None
    six_to_twelve_months: float | None = None
    one_to_two_years: float | None = None
    two_to_three_years: float | None = None
    more_than_three_years: float | None = None
    total_gross: float | None = None
    allowance_doubtful: float | None = None
    net_total: float | None = None
    year: str


class TradePayablesAgeing(BaseModel):
    not_due: float | None = None
    zero_to_six_months: float | None = None
    six_to_twelve_months: float | None = None
    one_to_two_years: float | None = None
    two_to_three_years: float | None = None
    more_than_three_years: float | None = None
    total_mse: float | None = None
    total_others: float | None = None
    total: float | None = None
    year: str


class CWIPProject(BaseModel):
    project_name: str | None = None
    age_bucket: str  # 'less_than_1_year', '1_to_2_years', '2_to_3_years', 'more_than_3_years'
    amount: float


class CWIPAgeing(BaseModel):
    projects: list[CWIPProject] = Field(default_factory=list)
    total: float | None = None
    year: str | None = None


class ContingentLiabilities(BaseModel):
    income_tax: float | None = None
    service_tax: float | None = None
    gst: float | None = None
    others: float | None = None
    total: float | None = None
    total_prior: float | None = None


class CSRDetails(BaseModel):
    obligation: float | None = None
    amount_spent: float | None = None
    amount_unspent: float | None = None
    carry_forward_years: list[dict] = Field(default_factory=list)


class MSMEDDisclosure(BaseModel):
    principal_unpaid: float | None = None
    interest_unpaid: float | None = None
    interest_accrued_unpaid: float | None = None
    interest_further_due: float | None = None


class ActuarialAssumptions(BaseModel):
    discount_rate_current: float | None = None
    discount_rate_prior: float | None = None
    salary_escalation_shore_current: float | None = None
    salary_escalation_shore_prior: float | None = None


class DisclosedRatio(BaseModel):
    """A ratio disclosed by the company itself (Schedule III analytical ratios note)."""
    name: str
    current: float | None = None
    prior: float | None = None
    variance_pct: float | None = None
    reason_for_variance: str | None = None


class CompanyRatios(BaseModel):
    ratios: list[DisclosedRatio] = Field(default_factory=list)
    year: str | None = None


# ── Layer 4: Analytics ───────────────────────────────────────────────

class MovementRecord(BaseModel):
    canonical_key: str
    display_label: str
    current: float | None
    prior: float | None
    absolute_change: float | None
    pct_change: float | None          # None if prior is zero or None
    materiality_pct: float | None     # abs(current) / total_assets * 100
    note_ref: str | None = None       # as extracted from the source table, e.g. "7a"
    prior_suppressed: bool = False    # True if a prior value was extracted but discarded as implausible


class ComputedRatios(BaseModel):
    current_ratio_current: float | None = None
    current_ratio_prior: float | None = None
    quick_ratio_current: float | None = None
    quick_ratio_prior: float | None = None
    debt_equity_current: float | None = None
    debt_equity_prior: float | None = None
    interest_coverage_current: float | None = None
    interest_coverage_prior: float | None = None
    debtor_days_current: float | None = None
    debtor_days_prior: float | None = None
    creditor_days_current: float | None = None
    creditor_days_prior: float | None = None
    net_profit_margin_current: float | None = None
    net_profit_margin_prior: float | None = None
    roce_current: float | None = None
    roce_prior: float | None = None
    cfo_to_pat_current: float | None = None
    cfo_to_pat_prior: float | None = None


class StructuredTables(BaseModel):
    """Container for all note-specific structured tables parsed for a document."""
    trade_receivables_ageing: TradeReceivablesAgeing | None = None
    trade_payables_ageing: TradePayablesAgeing | None = None
    cwip_ageing: CWIPAgeing | None = None
    contingent_liabilities: ContingentLiabilities | None = None
    csr_details: CSRDetails | None = None
    msmed_disclosure: MSMEDDisclosure | None = None
    actuarial_assumptions: ActuarialAssumptions | None = None
    company_ratios: CompanyRatios | None = None
