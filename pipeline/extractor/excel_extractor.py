"""Excel extraction: openpyxl for structure/comments, preserving merged cells."""
import openpyxl
from loguru import logger

from models.financial import CellComment, ExtractedWorkbook, SheetData


def _detect_sheet_type(sheet_name: str) -> str:
    name = sheet_name.lower()
    if "balance" in name:
        return "balance_sheet"
    if "profit" in name or "loss" in name or "p&l" in name or "pnl" in name:
        return "pnl"
    if "cash" in name:
        return "cash_flow"
    return "unknown"


def _apply_merged_cells(ws) -> None:
    """Copy the top-left value of each merged range to every cell in the range."""
    for merged_range in list(ws.merged_cells.ranges):
        top_left = ws.cell(row=merged_range.min_row, column=merged_range.min_col)
        value = top_left.value
        for row in ws.iter_rows(
            min_row=merged_range.min_row,
            max_row=merged_range.max_row,
            min_col=merged_range.min_col,
            max_col=merged_range.max_col,
        ):
            for cell in row:
                cell.value = value


def _extract_comments(ws, sheet_name: str) -> list[CellComment]:
    comments: list[CellComment] = []
    for row in ws.iter_rows():
        for cell in row:
            if cell.comment:
                comments.append(
                    CellComment(
                        sheet=sheet_name,
                        cell_ref=cell.coordinate,
                        value=cell.value,
                        comment_text=cell.comment.text,
                        comment_author=cell.comment.author,
                    )
                )
    return comments


def extract_excel(excel_path: str) -> ExtractedWorkbook:
    try:
        wb = openpyxl.load_workbook(excel_path, data_only=True)
    except Exception as e:
        logger.error(f"Failed to load workbook {excel_path}: {e}")
        return ExtractedWorkbook(sheets=[], all_comments=[])

    sheets: list[SheetData] = []
    all_comments: list[CellComment] = []

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        try:
            _apply_merged_cells(ws)
        except Exception as e:
            logger.warning(f"Merged-cell resolution failed for sheet {sheet_name}: {e}")

        comments = _extract_comments(ws, sheet_name)
        all_comments.extend(comments)

        all_rows = list(ws.iter_rows(values_only=True))
        if not all_rows:
            sheets.append(
                SheetData(
                    sheet_name=sheet_name,
                    sheet_type=_detect_sheet_type(sheet_name),
                    headers=[],
                    rows=[],
                    comments=comments,
                )
            )
            continue

        headers = [str(h) if h is not None else "" for h in all_rows[0]]
        rows = [list(row) for row in all_rows[1:]]

        sheets.append(
            SheetData(
                sheet_name=sheet_name,
                sheet_type=_detect_sheet_type(sheet_name),
                headers=headers,
                rows=rows,
                comments=comments,
            )
        )

    return ExtractedWorkbook(sheets=sheets, all_comments=all_comments)
