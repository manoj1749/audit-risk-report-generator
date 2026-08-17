"""audit-risk-report-generator — Gradio entry point.

Gradio (not Streamlit) so the app can run as a native Hugging Face Space SDK —
HF's Space-creation wizard only offers Static/Gradio/Docker, and Docker requires
account verification. The pipeline underneath (Layers 1-5) is unchanged.
"""
import asyncio
import io
import json
import os
import queue
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

import gradio as gr
import pandas as pd
from loguru import logger
from PIL import Image

PRIMARY_DOC_TYPES = [".pdf", ".docx", ".png", ".jpg", ".jpeg"]

INFO_TEXT = (
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

RISK_ICON = {"High": "🔴", "Medium": "🟡", "Low": "🟢"}

# Number of outputs shared by every yield in run_pipeline — keep in sync with
# the `outputs=[...]` list passed to run_btn.click() below.
_N_OUTPUTS = 17


def _extract_primary_document(path: str):
    """Dispatch to the right Layer-1 extractor based on file extension.

    The annual report (face statements + notes) may arrive as a PDF, a Word
    document, or a photographed/scanned image — every path returns the same
    ExtractedDocument shape so the rest of the pipeline is agnostic to source
    format.
    """
    suffix = Path(path).suffix.lower()
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


def _png_bytes_to_image(png_bytes: bytes | None) -> Image.Image | None:
    if not png_bytes:
        return None
    return Image.open(io.BytesIO(png_bytes))


def _build_observations_markdown(observations) -> str:
    """Render each observation as a collapsible <details> block (native HTML,
    works inside gr.Markdown regardless of how many observations there are —
    unlike native Gradio components, this doesn't need a fixed component count)."""
    if not observations:
        return "_No observations generated._"

    blocks = []
    for i, obs in enumerate(observations, 1):
        icon = RISK_ICON[obs.risk_rating]
        note_line = f"\n\n📄 {obs.note_ref}" if obs.note_ref else ""
        evidence_json = json.dumps(obs.evidence, indent=2, default=str)
        open_attr = " open" if obs.risk_rating == "High" else ""
        blocks.append(f"""<details{open_attr}>
<summary><strong>{icon} {i}. {obs.area} — {obs.risk_rating}</strong></summary>

**Observation**

{obs.observation}{note_line}

**Standard Reference**

> {obs.standard_reference}

**Recommendation**

> {obs.recommendation}

<details>
<summary>Evidence data</summary>

```json
{evidence_json}
```
</details>
</details>
""")
    return "\n".join(blocks)


def _movements_dataframe(key_movements) -> pd.DataFrame:
    rows = [{
        "Line Item": m.display_label,
        "Current (₹ lakh)": f"{m.current:,.0f}" if m.current else "-",
        "Prior (₹ lakh)": f"{m.prior:,.0f}" if m.prior else "-",
        "Change %": f"{m.pct_change:+.1f}%" if m.pct_change else "-",
        "Materiality %": f"{m.materiality_pct:.1f}%" if m.materiality_pct else "-",
    } for m in sorted(key_movements, key=lambda x: abs(x.pct_change or 0), reverse=True)]
    return pd.DataFrame(rows)


def _ratios_dataframe(ratios) -> pd.DataFrame:
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
    return pd.DataFrame([{
        "Ratio": name,
        "Current": f"{curr:.2f}" if curr is not None else "-",
        "Prior": f"{prior:.2f}" if prior is not None else "-",
    } for name, curr, prior in ratio_rows])


def run_pipeline(primary_path, excel_path, progress: gr.Progress = gr.Progress()):
    log_lines: list[str] = []

    def log(msg: str) -> str:
        log_lines.append(msg)
        return "\n".join(log_lines)

    def unchanged(status_text: str) -> tuple:
        return (status_text,) + (gr.update(),) * (_N_OUTPUTS - 1)

    if not primary_path:
        yield unchanged(log("❌ Please upload the required document (Annual Report / Notes & Schedules)."))
        return

    stage_t = time.time()

    def _stage_done(stage_name: str) -> None:
        nonlocal stage_t
        now = time.time()
        logger.info(f"STAGE_TIMING: {stage_name} took {now - stage_t:.1f}s")
        stage_t = now

    try:
        yield unchanged(log("📄 Extracting document..."))
        extracted = _extract_primary_document(primary_path)
        _stage_done("extraction")
        yield unchanged(log(f"✅ Extracted {extracted.total_pages} pages ({extracted.extraction_method})"))

        yield unchanged(log("🗂 Parsing notes structure..."))
        from pipeline.segmenter.note_parser import parse_notes
        from pipeline.segmenter.xref_graph import build_xref_graph
        notes = parse_notes(extracted)
        xref = build_xref_graph(notes)
        _stage_done("note_segmentation")
        yield unchanged(log(f"✅ Found {len(notes)} notes, {xref.graph.number_of_edges()} cross-references"))

        yield unchanged(log("🔢 Normalizing financial data..."))
        from pipeline.normalizer.line_item_mapper import map_all_items
        from pipeline.normalizer.table_extractor import extract_all_tables
        mapped_items = map_all_items(extracted, excel_path, notes)
        structured_tables = extract_all_tables(notes)
        _stage_done("normalization")
        yield unchanged(log(
            f"✅ Mapped {len([m for m in mapped_items.values() if m.canonical_key])} "
            f"of {len(mapped_items)} line items"
        ))

        yield unchanged(log("📊 Running analytical checks..."))
        from pipeline.analytics.horizontal import compute_movements
        from pipeline.analytics.ratios import compute_ratios
        from pipeline.analytics.flags import generate_all_flags
        from pipeline.analytics.consistency import run_consistency_checks
        movements = compute_movements(mapped_items)
        ratios = compute_ratios(mapped_items)
        flags = generate_all_flags(movements, mapped_items, structured_tables, notes)
        flags += run_consistency_checks(structured_tables, notes, extracted.full_text)
        _stage_done("analytics_flagging")
        yield unchanged(log(
            f"✅ {len(flags)} flags generated "
            f"({sum(1 for f in flags if f.severity == 'High')} High, "
            f"{sum(1 for f in flags if f.severity == 'Medium')} Medium, "
            f"{sum(1 for f in flags if f.severity == 'Low')} Low)"
        ))

        yield unchanged(log(f"✍️ Generating {len(flags)} observations..."))
        from pipeline.generator.observation_gen import generate_all_observations

        # CPU-only local-LLM generation can take minutes per observation, with
        # nothing else to show progress on — run it off-thread so this generator
        # can keep yielding a live per-observation timer instead of one opaque
        # message for the whole (potentially 30-60min) batch.
        progress_queue: queue.Queue = queue.Queue()
        gen_result: dict = {}

        def _progress_cb(done: int, total: int) -> None:
            progress_queue.put((done, total))

        def _run_generation() -> None:
            try:
                gen_result["observations"] = asyncio.run(
                    generate_all_observations(flags, notes, xref, progress_cb=_progress_cb)
                )
            except Exception as e:
                gen_result["error"] = e
            finally:
                progress_queue.put(None)

        gen_thread = threading.Thread(target=_run_generation, daemon=True)
        start_time = time.time()
        gen_thread.start()

        while True:
            item = progress_queue.get()
            if item is None:
                break
            done, total = item
            mins, secs = divmod(int(time.time() - start_time), 60)
            progress(done / total, desc=f"Observation {done}/{total} · {mins}m {secs:02d}s elapsed")
            log_lines[-1] = f"✍️ Generating observation {done}/{total}… ({mins}m {secs:02d}s elapsed)"
            yield unchanged("\n".join(log_lines))

        gen_thread.join()
        if "error" in gen_result:
            raise gen_result["error"]
        observations = gen_result["observations"]
        _stage_done("llm_generation")
        yield unchanged(log(f"✅ Generated {len(observations)} observations"))

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
    except Exception as e:
        yield unchanged(log(f"❌ Analysis failed: {e}"))
        return

    from export.docx_exporter import export_report_docx
    docx_bytes = export_report_docx(report)
    tmp_dir = Path(tempfile.mkdtemp())
    docx_path = tmp_dir / f"audit_risk_{report.period or 'report'}.docx"
    docx_path.write_bytes(docx_bytes)

    from export.charts import generate_movements_chart, generate_ratios_chart, generate_risk_distribution_chart
    risk_chart = _png_bytes_to_image(generate_risk_distribution_chart(report.summary))
    movements_chart = _png_bytes_to_image(generate_movements_chart(report.key_movements))
    ratios_chart = _png_bytes_to_image(generate_ratios_chart(report.ratios))

    header_lines = []
    if report.company_name:
        header_lines.append(f"### {report.company_name}")
    if report.period:
        header_lines.append(f"Period: {report.period} · Generated: {report.generated_at.strftime('%d %b %Y %H:%M')}")
    header_md = "\n\n".join(header_lines)

    mov_df = _movements_dataframe(report.key_movements) if report.key_movements else pd.DataFrame(
        columns=["Line Item", "Current (₹ lakh)", "Prior (₹ lakh)", "Change %", "Materiality %"]
    )

    yield (
        log("✅ Analysis complete"),
        gr.update(visible=False),  # upload_group
        gr.update(visible=True),   # results_group
        header_md,
        str(docx_path),
        f"⚠️ {report.disclaimer}",
        report.summary["High"],
        report.summary["Medium"],
        report.summary["Low"],
        report.summary["total"],
        risk_chart,
        _build_observations_markdown(report.observations),
        mov_df,
        movements_chart,
        _ratios_dataframe(report.ratios),
        ratios_chart,
        [f.model_dump() for f in flags],
    )


def reset_ui():
    return gr.update(visible=True), gr.update(visible=False), None, None, ""


with gr.Blocks(title="audit-risk-report-generator") as demo:
    gr.Markdown(
        "# 🔍 audit-risk-report-generator\n"
        "Automated audit risk assessment · Ind AS / IFRS · Preliminary review"
    )

    with gr.Group(visible=True) as upload_group:
        with gr.Row():
            with gr.Column(scale=2):
                excel_file = gr.File(
                    label="Financial Statements — Excel (optional, if not already in the PDF below)",
                    file_types=[".xlsx", ".xls"],
                    type="filepath",
                )
                primary_file = gr.File(
                    label="Annual Report / Notes & Schedules — PDF, Word, or image (required)",
                    file_types=PRIMARY_DOC_TYPES,
                    type="filepath",
                )
                run_btn = gr.Button("Run Audit Risk Analysis", variant="primary")
            with gr.Column(scale=1):
                gr.Markdown(INFO_TEXT)
        status_log = gr.Textbox(label="Progress", lines=8, interactive=False)

    with gr.Group(visible=False) as results_group:
        header_md = gr.Markdown()
        with gr.Row():
            download_file = gr.File(label="⬇ Download Report (.docx)")
            new_analysis_btn = gr.Button("New Analysis")
        disclaimer_md = gr.Markdown()

        with gr.Row():
            high_num = gr.Number(label="High Risk", interactive=False)
            med_num = gr.Number(label="Medium Risk", interactive=False)
            low_num = gr.Number(label="Low Risk", interactive=False)
            total_num = gr.Number(label="Total Observations", interactive=False)
        risk_chart_img = gr.Image(label="Risk Distribution", interactive=False, show_label=True)

        with gr.Tabs():
            with gr.Tab("Observations"):
                obs_md = gr.Markdown()
            with gr.Tab("Key Movements"):
                mov_df_out = gr.Dataframe(interactive=False, wrap=True)
                mov_chart_img = gr.Image(interactive=False, show_label=False)
            with gr.Tab("Ratios"):
                rat_df_out = gr.Dataframe(interactive=False, wrap=True)
                rat_chart_img = gr.Image(interactive=False, show_label=False)
            with gr.Tab("Raw Flags"):
                gr.Markdown("All analytical flags triggered before observation generation")
                flags_json = gr.JSON()

    run_btn.click(
        fn=run_pipeline,
        inputs=[primary_file, excel_file],
        outputs=[
            status_log, upload_group, results_group, header_md, download_file, disclaimer_md,
            high_num, med_num, low_num, total_num, risk_chart_img,
            obs_md, mov_df_out, mov_chart_img, rat_df_out, rat_chart_img, flags_json,
        ],
    )
    new_analysis_btn.click(
        fn=reset_ui,
        outputs=[upload_group, results_group, primary_file, excel_file, status_log],
    )

demo.queue()

if __name__ == "__main__":
    # Cloud Run injects $PORT (default 8080) and requires binding 0.0.0.0;
    # unset locally, so this falls back to Gradio's normal 127.0.0.1:7860.
    demo.launch(
        server_name=os.environ.get("GRADIO_SERVER_NAME", "127.0.0.1"),
        server_port=int(os.environ.get("PORT", 7860)),
    )
