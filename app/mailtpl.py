def esg_team_notice(f: dict, original_filename: str) -> tuple[str, str]:
    # verbatim CF7 form 19142 _mail (esg.md §B1); subject ends with the Devanagari danda
    subject = f"ESG Rating Form Submission - {f['name']}।"
    body = (
        f"Name: {f['name']}\nEmail: {f['email']}\nDesignation: {f['designation']}\n"
        f"Company Name: {f['company_name']}\nMobile Number: {f['mobile_number']}\n"
        f"Report Financial Year: {f['report_year']}\nReport: {original_filename}\n\nThank you!"
    )
    return subject, body


def bfsi_team_notice(f: dict, industry_label: str) -> tuple[str, str]:
    # verbatim submit.php:58
    subject = f"BFSI ESG Submission — {f['borrower_name']}"
    body = (
        "New BFSI Credit Risk submission\n\n"
        f"Borrower: {f['borrower_name']}\nCIN/GSTIN: {f['cin_gstin']}\n"
        f"Industry: {industry_label} / {f['sub_sector']}\n"
        f"Loan: {f['loan_type']} — ₹{f['loan_amount']} ({f['loan_purpose']})\n"
        f"Outstanding: ₹{f['outstanding_loans']}\nEmail: {f['contact_email']}\n\n"
        "Review it in the dashboard → BFSI Calculator."
    )
    return subject, body


_SIGN = ("\n\nFor any questions, reply to this email or contact us at info@esgratings.co.in / +91 8587898484.\n\n"
         "Regards,\nESG Ratings — CFC (SEBI Registered ERP)\nwww.esgratings.co.in")


def esg_report_mail(name: str, company: str, fy: str) -> tuple[str, str]:
    return (f"Your ESG Rating Report — {company}",
            f"Dear {name},\n\nPlease find attached the ESG Rating Report for {company} (FY {fy})." + _SIGN)


def bfsi_report_mail(borrower: str) -> tuple[str, str]:
    return (f"Your BFSI ESG Credit Risk Report — {borrower}",
            f"Dear Sir/Madam,\n\nPlease find attached the BFSI ESG Credit Risk Report for {borrower}." + _SIGN)


# Default Send report emails (app/core/mail_templates.py). The admin edits them in the
# Send dialog and can save their own version; {placeholders} are filled per report.
_REPORT_SIGN = (
    "For any questions, contact us at info@esgratings.co.in or +91 8587898484.\n\n"
    "Regards,\nESG Ratings — CFC (SEBI Registered ERP)\nwww.esgratings.co.in"
)

ESG_REPORT_SUBJECT = "ESG Rating Report — {company} (FY {year})"
ESG_REPORT_BODY = (
    "Dear {name},\n\n"
    "Thank you for choosing ESG Ratings.\n\n"
    "Please find attached the ESG assessment of {company} for FY {year}:\n"
    "• ESG Rating Report — the one-page summary of the overall ESG score, grade and pillar scores.\n"
    "• Detailed Report — the KPI-by-KPI assessment, page-by-page scores and the reasoning "
    "behind each score.\n\n"
    "The assessment is based on the sustainability disclosures in the report you shared with us. "
    "If you would like to discuss the results or how to improve the rating, simply reply to this email.\n\n"
    + _REPORT_SIGN
)

BFSI_REPORT_SUBJECT = "BFSI ESG Credit Risk Report — {borrower}"
BFSI_REPORT_BODY = (
    "Dear Sir/Madam,\n\n"
    "Please find attached the BFSI ESG Credit Risk assessment of {borrower}:\n"
    "• One-Page Rating Report — the overall ESG credit risk score, grade and pillar scores.\n"
    "• Detailed Report — the pillar marks, KPI assessment, key risks, lending recommendation "
    "and the page-by-page scoring rationale.\n\n"
    "The assessment is based on the BRSR / sustainability report submitted with the application. "
    "Reply to this email if you would like to discuss the results.\n\n"
    + _REPORT_SIGN
)


def contact_notice(f: dict) -> tuple[str, str]:
    return (f"Website enquiry — {f['name']}",
            f"Name: {f['name']}\nEmail: {f['email']}\nNumber: {f['number']}\n\nMessage:\n{f['message']}")
