# app/core/page_scores.py
# The page-scores export both calculators share: one CSV row per scored page and
# category, with the same columns for ESG and BFSI.
import csv
import io

from fastapi.responses import StreamingResponse

HEADER = ["Filename", "Category", "Text", "Page No", "Reason", "Score", "KPIs Present",
          "Positive Keywords", "Negative Keywords"]


def kpis_cell(kpis) -> str:
    """A page's KPIs (app/core/kpis.py) as one cell. "; " separates them because KPI
    names contain commas. Pages scored before KPI tagging existed export an empty cell."""
    if not isinstance(kpis, list):
        return ""
    return "; ".join(str(k) for k in kpis if str(k).strip())


def keywords_cell(keywords) -> str:
    return ", ".join(str(k) for k in keywords) if isinstance(keywords, list) else ""


def csv_response(rows: list[list], filename: str) -> StreamingResponse:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(HEADER)
    writer.writerows(rows)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
