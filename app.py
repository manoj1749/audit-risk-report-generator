"""audit-risk-report-generator — Streamlit entry point."""
import asyncio
import tempfile
from datetime import datetime
from pathlib import Path

import streamlit as st

PRIMARY_DOC_TYPES = ["pdf", "docx", "png", "jpg", "jpeg"]


def extract_primary_document(uploaded_file):
    """Dispatch to the right Layer-1 extractor based on file extension.

    The annual report (face statements + notes) may arrive as a PDF, a Word
    document, or a photographed/scanned image — every path returns the same
    ExtractedDocument shape so the rest of the pipeline is agnostic to source
    format.
    """
    suffix = Path(uploaded_file.name).suffix.lower()
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(uploaded_file.read())
        path = f.name

    if suffix == ".pdf":
        from pipeline.extractor.pdf_extractor import extract_pdf
        return extract_pdf(path)
    elif suffix == ".docx":
        from pipeline.extractor.docx_extractor import extract_docx
        return extract_docx(path)
    elif suffix in (".png", ".jpg", ".jpeg"):
        from pipeline.extractor.pdf_extractor import extract_image
        return extract_image(path)
    else:
        raise ValueError(f"Unsupported file type: {suffix}")

st.set_page_config(
    page_title="audit-risk-report-generator",
    page_icon="🔍",
    layout="wide",
)

# ── Session state ──
if "report" not in st.session_state:
    st.session_state.report = None
if "running" not in st.session_state:
    st.session_state.running = False
if "flags" not in st.session_state:
    st.session_state.flags = []


def run_pipeline(primary_file, excel_file) -> None:
    st.session_state.running = True

    with st.status("Running analysis...", expanded=True) as status:

        excel_path = None
        if excel_file:
            with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
                f.write(excel_file.read())
                excel_path = f.name

        # Layer 1
        st.write("📄 Extracting document...")
        extracted = extract_primary_document(primary_file)
        st.write(f"✅ Extracted {extracted.total_pages} pages ({extracted.extraction_method})")

        # Layer 2
        st.write("🗂 Parsing notes structure...")
        from pipeline.segmenter.note_parser import parse_notes
        from pipeline.segmenter.xref_graph import build_xref_graph
        notes = parse_notes(extracted)
        xref = build_xref_graph(notes)
        st.write(f"✅ Found {len(notes)} notes, {xref.graph.number_of_edges()} cross-references")

        # Layer 3
        st.write("🔢 Normalizing financial data...")
        from pipeline.normalizer.line_item_mapper import map_all_items
        from pipeline.normalizer.table_extractor import extract_all_tables
        mapped_items = map_all_items(extracted, excel_path)
        structured_tables = extract_all_tables(notes)
        st.write(
            f"✅ Mapped {len([m for m in mapped_items.values() if m.canonical_key])} "
            f"of {len(mapped_items)} line items"
        )

        # Layer 4
        st.write("📊 Running analytical checks...")
        from pipeline.analytics.horizontal import compute_movements
        from pipeline.analytics.ratios import compute_ratios
        from pipeline.analytics.flags import generate_all_flags
        from pipeline.analytics.consistency import run_consistency_checks
        movements = compute_movements(mapped_items)
        ratios = compute_ratios(mapped_items)
        flags = generate_all_flags(movements, mapped_items, structured_tables, notes)
        flags += run_consistency_checks(structured_tables, notes, extracted.full_text)
        st.session_state.flags = flags
        st.write(
            f"✅ {len(flags)} flags generated "
            f"({sum(1 for f in flags if f.severity == 'High')} High, "
            f"{sum(1 for f in flags if f.severity == 'Medium')} Medium, "
            f"{sum(1 for f in flags if f.severity == 'Low')} Low)"
        )

        # Layer 5
        st.write(f"✍️ Generating {len(flags)} observations...")
        from pipeline.generator.observation_gen import generate_all_observations
        observations = asyncio.run(generate_all_observations(flags, notes, xref))
        st.write(f"✅ Generated {len(observations)} observations")

        # Compile report
        from models.report import AuditReport
        report = AuditReport(
            company_name=extracted.company_name,
            period=extracted.period,
            generated_at=datetime.now(),
            extraction_method=extracted.extraction_method,
            summary={
                "High": sum(1 for o in observations if o.risk_rating == "High"),
                "Medium": sum(1 for o in observations if o.risk_rating == "Medium"),
                "Low": sum(1 for o in observations if o.risk_rating == "Low"),
                "total": len(observations),
            },
            observations=observations,
            key_movements=[m for m in movements.values() if m.pct_change and abs(m.pct_change) > 10],
            ratios=ratios,
            flags_triggered=len(flags),
            observations_generated=len(observations),
        )

        st.session_state.report = report
        status.update(label="Analysis complete", state="complete")

    st.rerun()


st.title("🔍 audit-risk-report-generator")
st.caption("Automated audit risk assessment · Ind AS / IFRS · Preliminary review")

if st.session_state.report is None:
    col1, col2 = st.columns([2, 1])

    with col1:
        excel_file = st.file_uploader(
            "Base Financial Statements — Excel (recommended)",
            type=["xlsx", "xls"],
            help="The base sheet — Balance Sheet, P&L, and Cash Flow, read line by "
                 "line. This is the main source of the figures used throughout the "
                 "analysis; always upload it when you have it, since numbers read "
                 "straight from Excel cells are more reliable than numbers pulled "
                 "out of PDF tables.",
        )
        primary_file = st.file_uploader(
            "Notes & Schedules — PDF, Word, or image (required)",
            type=PRIMARY_DOC_TYPES,
            help="The numbered notes to accounts, linked to the balance sheet by "
                 "note number (e.g. Note 7(f), Note 27). Many of the risk checks — "
                 "MSME payables, CSR spend, contingent liabilities, ageing schedules "
                 "— depend entirely on this document, so it's always required, even "
                 "when Excel is also provided. Can arrive as a PDF, a Word document "
                 "(.docx), or a scanned/photographed image. If it also contains the "
                 "face financial statements, that's fine too — Excel above is only "
                 "needed when it doesn't.",
        )

    with col2:
        st.info(
            "**What to upload**\n\n"
            "This is normally two documents: the base Excel sheet with the balance "
            "sheet, P&L, and cash flow — the main source of every figure in the "
            "report — plus the linked notes/schedules document, referenced by note "
            "number, which the note-specific risk checks (MSME, CSR, contingent "
            "liabilities, ageing) depend on. The notes/schedules document can be a "
            "PDF, Word file, or a scanned/photographed image, and is always "
            "required.\n\n"
            "If the balance sheet and P&L aren't in Excel — e.g. everything, "
            "including the face statements, is in one PDF — just upload that PDF "
            "as the required document and skip the Excel upload."
        )

    if primary_file:
        if st.button("Run Audit Risk Analysis", type="primary", use_container_width=True):
            run_pipeline(primary_file, excel_file)

if st.session_state.report:
    report = st.session_state.report

    col1, col2, col3 = st.columns([3, 1, 1])
    with col1:
        if report.company_name:
            st.subheader(report.company_name)
        if report.period:
            st.caption(
                f"Period: {report.period} · "
                f"Generated: {report.generated_at.strftime('%d %b %Y %H:%M')}"
            )
    with col2:
        from export.docx_exporter import export_report_docx
        docx_bytes = export_report_docx(report)
        st.download_button(
            "⬇ Download Report",
            data=docx_bytes,
            file_name=f"audit_risk_{report.period or 'report'}.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    with col3:
        if st.button("New Analysis"):
            st.session_state.report = None
            st.session_state.flags = []
            st.rerun()

    st.warning(report.disclaimer)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("High Risk", report.summary["High"])
    c2.metric("Medium Risk", report.summary["Medium"])
    c3.metric("Low Risk", report.summary["Low"])
    c4.metric("Total Observations", report.summary["total"])

    tab_obs, tab_mov, tab_rat, tab_raw = st.tabs([
        f"Observations ({report.summary['total']})",
        "Key Movements",
        "Ratios",
        "Raw Flags",
    ])

    with tab_obs:
        risk_color = {"High": "🔴", "Medium": "🟡", "Low": "🟢"}

        for i, obs in enumerate(report.observations, 1):
            icon = risk_color[obs.risk_rating]
            with st.expander(
                f"{icon} **{i}. {obs.area}** — {obs.risk_rating}",
                expanded=(obs.risk_rating == "High"),
            ):
                st.markdown("**Observation**")
                st.write(obs.observation)

                col_a, col_b = st.columns(2)
                with col_a:
                    st.markdown("**Standard Reference**")
                    st.info(obs.standard_reference)
                with col_b:
                    st.markdown("**Recommendation**")
                    st.success(obs.recommendation)

                with st.expander("Evidence data", expanded=False):
                    st.json(obs.evidence)

        if not report.observations:
            st.info("No observations generated.")

    with tab_mov:
        if report.key_movements:
            import pandas as pd
            df = pd.DataFrame([{
                "Line Item": m.display_label,
                "Current (₹ lakh)": f"{m.current:,.0f}" if m.current else "-",
                "Prior (₹ lakh)": f"{m.prior:,.0f}" if m.prior else "-",
                "Change %": f"{m.pct_change:+.1f}%" if m.pct_change else "-",
                "Materiality %": f"{m.materiality_pct:.1f}%" if m.materiality_pct else "-",
            } for m in sorted(
                report.key_movements,
                key=lambda x: abs(x.pct_change or 0),
                reverse=True,
            )])
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("No significant movements detected.")

    with tab_rat:
        ratios = report.ratios
        ratio_rows = [
            ("Current Ratio", ratios.current_ratio_current, ratios.current_ratio_prior),
            ("Quick Ratio", ratios.quick_ratio_current, ratios.quick_ratio_prior),
            ("Debt-Equity Ratio", ratios.debt_equity_current, ratios.debt_equity_prior),
            ("Interest Coverage", ratios.interest_coverage_current, ratios.interest_coverage_prior),
            ("Debtor Days", ratios.debtor_days_current, ratios.debtor_days_prior),
            ("Creditor Days", ratios.creditor_days_current, ratios.creditor_days_prior),
            ("Net Profit Margin %", ratios.net_profit_margin_current, ratios.net_profit_margin_prior),
            ("ROCE %", ratios.roce_current, ratios.roce_prior),
            ("CFO / PAT", ratios.cfo_to_pat_current, ratios.cfo_to_pat_prior),
        ]
        import pandas as pd
        df_ratios = pd.DataFrame([{
            "Ratio": name,
            "Current": f"{curr:.2f}" if curr is not None else "-",
            "Prior": f"{prior:.2f}" if prior is not None else "-",
        } for name, curr, prior in ratio_rows])
        st.dataframe(df_ratios, use_container_width=True, hide_index=True)

    with tab_raw:
        st.caption("All analytical flags triggered before observation generation")
        st.json([f.model_dump() for f in st.session_state.get("flags", [])])
