# Verbatim port of bfsi-calculator/config/options.php (business config, not secrets).
# BFSI_QUESTIONS_STEP2 and BFSI_STEP3_SETS are dead code in the PHP source (the
# questionnaire step was never wired up) and are intentionally not ported — see
# docs/analysis/bfsi.md §4d.

MAX_UPLOAD_MB = 20

LOAN_PURPOSES: list[str] = [
    "Capacity Expansion", "Working Capital", "Land Purchase", "Building Construction",
    "Plant & Machinery", "Renewable Energy", "Warehouse", "New Manufacturing Unit",
    "Acquisition", "Refinancing", "General Corporate Purpose",
]

LOAN_TYPES: list[str] = [
    "Working Capital", "Term Loan", "Project Finance", "Machinery Loan", "Equipment Finance",
    "Commercial Vehicle Loan", "Construction Finance", "Infrastructure Finance",
    "Renewable Energy Loan", "MSME Loan", "Agriculture Loan", "Home Loan (Builder)",
    "Real Estate Finance", "Supply Chain Finance", "Trade Finance", "Export Finance",
    "Gold Loan (Corporate)", "Lease Finance", "Venture Debt", "Bridge Finance",
]

# E/S/G percentages — "Recommended ESG Weightage by Loan Type" table
WEIGHTAGE: dict[str, dict[str, int]] = {
    "Working Capital": {"e": 20, "s": 30, "g": 50},
    "MSME Loan": {"e": 25, "s": 30, "g": 45},
    "Manufacturing": {"e": 45, "s": 25, "g": 30},
    "Infrastructure": {"e": 45, "s": 25, "g": 30},
    "Construction": {"e": 45, "s": 30, "g": 25},
    "Renewable Energy": {"e": 50, "s": 20, "g": 30},
    "Agriculture": {"e": 50, "s": 25, "g": 25},
    "Vehicle Finance": {"e": 35, "s": 25, "g": 40},
    "Commercial Real Estate": {"e": 40, "s": 25, "g": 35},
    "Trade Finance": {"e": 20, "s": 25, "g": 55},
    "Export Finance": {"e": 25, "s": 30, "g": 45},
    "Healthcare": {"e": 30, "s": 40, "g": 30},
    "Education": {"e": 20, "s": 50, "g": 30},
    "Technology / IT": {"e": 15, "s": 35, "g": 50},
}

LOANTYPE_TO_WEIGHTAGE: dict[str, str] = {
    "Working Capital": "Working Capital",
    "Term Loan": "Working Capital",
    "Project Finance": "Infrastructure",
    "Machinery Loan": "Manufacturing",
    "Equipment Finance": "Manufacturing",
    "Commercial Vehicle Loan": "Vehicle Finance",
    "Construction Finance": "Construction",
    "Infrastructure Finance": "Infrastructure",
    "Renewable Energy Loan": "Renewable Energy",
    "MSME Loan": "MSME Loan",
    "Agriculture Loan": "Agriculture",
    "Home Loan (Builder)": "Commercial Real Estate",
    "Real Estate Finance": "Commercial Real Estate",
    "Supply Chain Finance": "Trade Finance",
    "Trade Finance": "Trade Finance",
    "Export Finance": "Export Finance",
    "Gold Loan (Corporate)": "Working Capital",
    "Lease Finance": "Working Capital",
    "Venture Debt": "Technology / IT",
    "Bridge Finance": "Working Capital",
}

INDUSTRIES: dict[str, dict] = {
    "manufacturing": {
        "label": "Manufacturing", "step3_set": "manufacturing",
        "sub_sectors": ["Textiles", "Chemicals", "Auto Components", "Steel & Metals", "Cement",
                        "Pharmaceuticals Mfg", "Electronics", "FMCG", "Other Manufacturing"],
    },
    "construction": {
        "label": "Construction", "step3_set": "construction",
        "sub_sectors": ["Residential", "Commercial", "Industrial", "EPC Contractor", "Other Construction"],
    },
    "agriculture": {
        "label": "Agriculture", "step3_set": "agriculture",
        "sub_sectors": ["Crops", "Dairy", "Poultry", "Agro-processing", "Fisheries", "Other Agriculture"],
    },
    "renewable": {
        "label": "Renewable Energy", "step3_set": "renewable",
        "sub_sectors": ["Solar", "Wind", "Hydro", "Bioenergy", "Storage", "Other Renewable"],
    },
    "infrastructure": {
        "label": "Infrastructure", "step3_set": "construction",
        "sub_sectors": ["Roads", "Ports", "Airports", "Power T&D", "Water", "Urban Infra"],
    },
    "realestate": {
        "label": "Commercial Real Estate", "step3_set": "realestate",
        "sub_sectors": ["Office", "Retail", "Warehousing", "Hospitality", "Mixed Use"],
    },
    "vehicle": {
        "label": "Vehicle / Auto", "step3_set": "vehicle",
        "sub_sectors": ["Fleet Operator", "Logistics", "Passenger Transport", "Auto Dealer", "Other Vehicle"],
    },
    "it": {
        "label": "IT & Technology", "step3_set": "it",
        "sub_sectors": ["Software Services", "SaaS", "BPO/KPO", "Hardware", "Fintech", "Other IT"],
    },
    "healthcare": {
        "label": "Healthcare", "step3_set": "working",
        "sub_sectors": ["Hospitals", "Diagnostics", "Pharma Retail", "Medical Devices", "Other Healthcare"],
    },
    "education": {
        "label": "Education", "step3_set": "working",
        "sub_sectors": ["K-12", "Higher Education", "EdTech", "Vocational", "Other Education"],
    },
    "trade": {
        "label": "Trade & Export", "step3_set": "working",
        "sub_sectors": ["Wholesale", "Retail", "Import/Export", "Commodities", "Other Trade"],
    },
    "services": {
        "label": "Services / MSME", "step3_set": "working",
        "sub_sectors": ["Professional Services", "Financial Services", "Hospitality Services", "Media", "Other Services"],
    },
}


def options_payload() -> dict:
    return {
        "industries": INDUSTRIES,
        "loan_purposes": LOAN_PURPOSES,
        "loan_types": LOAN_TYPES,
        "weightage": WEIGHTAGE,
        "loantype_to_weightage": LOANTYPE_TO_WEIGHTAGE,
        "max_upload_mb": MAX_UPLOAD_MB,
    }
