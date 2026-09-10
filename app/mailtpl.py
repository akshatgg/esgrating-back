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


def contact_notice(f: dict) -> tuple[str, str]:
    return (f"Website enquiry — {f['name']}",
            f"Name: {f['name']}\nEmail: {f['email']}\nNumber: {f['number']}\n\nMessage:\n{f['message']}")
