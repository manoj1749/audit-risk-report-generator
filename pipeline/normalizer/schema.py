"""Canonical financial line item schema. Every extracted line item maps to one of these keys."""

CANONICAL_SCHEMA = {
    # Current Assets
    "cash_equivalents": ["cash and cash equivalents", "cash & cash equivalents"],
    "bank_balances_other": ["bank balances other than above", "other bank balances"],
    "trade_receivables": ["trade receivables", "sundry debtors", "accounts receivable"],
    "inventories": ["inventories", "stock", "fuel oil", "stores and spares"],
    "current_investments": ["current investments", "short term investments"],
    "other_financial_assets_current": ["other financial assets", "other current financial assets"],
    "other_current_assets": ["other current assets"],
    "loans_current": ["loans - current", "current loans"],
    "total_current_assets": ["total current assets"],

    # Non-current Assets
    "ppe_net": ["property plant and equipment", "property, plant and equipment",
                "tangible assets", "fixed assets"],
    "cwip": ["capital work-in-progress", "capital work in progress", "cwip"],
    "roa_net": ["right-of-use asset", "right of use assets", "rou asset"],
    "intangible_assets": ["other intangible assets", "intangible assets"],
    "equity_method_investments": ["investments accounted for using equity method",
                                   "investments in associates", "investments in joint ventures"],
    "non_current_investments": ["non-current investments", "long term investments"],
    "loans_non_current": ["loans - non-current", "non current loans"],
    "other_financial_assets_non_current": ["other financial assets - non-current"],
    "deferred_tax_assets": ["deferred tax assets"],
    "income_tax_assets": ["income tax assets"],
    "other_non_current_assets": ["other non-current assets", "other non current assets"],
    "total_non_current_assets": ["total non-current assets"],
    "total_assets": ["total assets"],

    # Equity
    "share_capital": ["equity share capital", "share capital"],
    "other_equity": ["other equity", "reserves and surplus"],
    "total_equity": ["total equity", "equity attributable to owners"],

    # Current Liabilities
    "borrowings_current": ["borrowings - current", "short term borrowings", "current borrowings",
                            "current maturities of long-term debt"],
    "lease_liabilities_current": ["lease liabilities - current"],
    # "dues to msme" deliberately excluded: rapidfuzz's WRatio scores it a
    # 85.5 match against "transfer to statutory reserve" (a Statement of
    # Changes in Equity line, nothing to do with payables) — just over the
    # FUZZY_MATCH_THRESHOLD of 85, confirmed on a real filing. The genuine
    # MSE-payables case this variant was meant for is already covered by
    # the exact-match override above (any label with "micro"+"enterprise"),
    # which every real-world label observed so far actually uses.
    "trade_payables_mse": ["trade payables - micro and small",
                            "micro enterprises and small enterprises"],
    "trade_payables_others": ["trade payables - others", "creditors other than micro"],
    "other_financial_liabilities_current": ["other financial liabilities - current"],
    "other_current_liabilities": ["other current liabilities"],
    "provisions_current": ["provisions - current"],
    "total_current_liabilities": ["total current liabilities"],

    # Non-current Liabilities
    "borrowings_non_current": ["borrowings - non-current", "long-term borrowings",
                                "long term borrowings"],
    "lease_liabilities_non_current": ["lease liabilities - non-current"],
    "other_financial_liabilities_non_current": ["other financial liabilities - non-current"],
    "provisions_non_current": ["provisions - non-current"],
    "deferred_tax_liabilities": ["deferred tax liabilities"],
    "other_non_current_liabilities": ["other non-current liabilities"],
    "total_non_current_liabilities": ["total non-current liabilities"],
    "total_liabilities": ["total liabilities"],

    # P&L
    "revenue_from_operations": ["revenue from operations"],
    "other_income": ["other income"],
    "total_income": ["total income"],
    "cost_of_services": ["cost of services rendered", "cost of materials consumed",
                          "cost of goods sold", "purchases"],
    "employee_benefits_expense": ["employee benefits expense", "employee benefit expense",
                                   "staff costs"],
    "finance_costs": ["finance costs", "interest and finance charges"],
    "depreciation_amortisation": ["depreciation and amortisation", "depreciation"],
    "other_expenses": ["other expenses"],
    "total_expenses": ["total expenses"],
    "pbt": ["profit before tax", "profit/(loss) before tax"],
    "share_of_jv_profit": ["share of net profit of associates",
                            "share of profit of associates and joint ventures"],
    "current_tax": ["current tax"],
    "tax_prior_years": ["tax pertaining to earlier years", "prior year tax"],
    "deferred_tax_expense": ["deferred tax"],
    "total_tax_expense": ["total tax expense", "income tax expense"],
    "pat": ["profit for the period", "profit/(loss) for the period",
            "profit after tax"],
    "oci_defined_benefit": ["remeasurements of defined benefit plans"],
    "oci_foreign_currency": ["foreign currency translation"],
    "oci_equity_method": ["share of oci of associates and joint ventures"],
    "total_oci": ["other comprehensive income for the period"],
    "total_comprehensive_income": ["total comprehensive income for the period"],
    "basic_eps": ["basic earnings per share"],
    "diluted_eps": ["diluted earnings per share"],

    # Cash Flow
    "cfo": ["net cash inflow from operating activities",
            "net cash from operating activities"],
    "cfi": ["net cash inflow from investing activities",
            "net cash from investing activities"],
    "cff": ["net cash inflow from financing activities",
            "net cash from financing activities"],
    "net_cash_change": ["net increase/(decrease) in cash"],
    "opening_cash": ["cash at beginning of year", "cash at the beginning"],
    "closing_cash": ["cash at end of year", "cash at the end of the year"],
}


DISPLAY_LABELS = {
    key: variants[0].title() for key, variants in CANONICAL_SCHEMA.items()
}

# Which face statement each canonical key belongs to. Used to stop a label match
# crossing statements — e.g. a cash flow statement's "(increase)/decrease in other
# financial assets" adjustment line sharing wording with the balance sheet's actual
# "other financial assets" balance, but representing a period movement, not a balance.
_BALANCE_SHEET_KEYS = {
    "cash_equivalents", "bank_balances_other", "trade_receivables", "inventories",
    "current_investments", "other_financial_assets_current", "other_current_assets",
    "loans_current", "total_current_assets",
    "ppe_net", "cwip", "roa_net", "intangible_assets", "equity_method_investments",
    "non_current_investments", "loans_non_current", "other_financial_assets_non_current",
    "deferred_tax_assets", "income_tax_assets", "other_non_current_assets",
    "total_non_current_assets", "total_assets",
    "share_capital", "other_equity", "total_equity",
    "borrowings_current", "lease_liabilities_current", "trade_payables_mse",
    "trade_payables_others", "other_financial_liabilities_current",
    "other_current_liabilities", "provisions_current", "total_current_liabilities",
    "borrowings_non_current", "lease_liabilities_non_current",
    "other_financial_liabilities_non_current", "provisions_non_current",
    "deferred_tax_liabilities", "other_non_current_liabilities",
    "total_non_current_liabilities", "total_liabilities",
}
_PNL_KEYS = {
    "revenue_from_operations", "other_income", "total_income", "cost_of_services",
    "employee_benefits_expense", "finance_costs", "depreciation_amortisation",
    "other_expenses", "total_expenses", "pbt", "share_of_jv_profit", "current_tax",
    "tax_prior_years", "deferred_tax_expense", "total_tax_expense", "pat",
    "oci_defined_benefit", "oci_foreign_currency", "oci_equity_method", "total_oci",
    "total_comprehensive_income", "basic_eps", "diluted_eps",
}
_CASH_FLOW_KEYS = {
    "cfo", "cfi", "cff", "net_cash_change", "opening_cash", "closing_cash",
}

CANONICAL_STATEMENT_TYPE: dict[str, str] = {
    **{k: "balance_sheet" for k in _BALANCE_SHEET_KEYS},
    **{k: "pnl" for k in _PNL_KEYS},
    **{k: "cash_flow" for k in _CASH_FLOW_KEYS},
}
