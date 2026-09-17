# BFSI Calculator: Scoring Methodology

## Summary

The BFSI calculator helps a lender judge the ESG risk of a borrower before giving a loan.
It scores the borrower's report **exactly like the ESG calculator**: each KPI is scored from 0 to 100, and a KPI that is missing scores 0.

The only difference is the final step. The overall score uses **weights that depend on the loan type**. For example, a Renewable Energy Loan gives more weight to Environment than a Working Capital loan.

The overall score then gives a grade and a lending recommendation.

## What the lender enters

- Borrower name, CIN/GSTIN and contact email
- Industry and sub-sector
- Loan amount, loan purpose and **loan type**
- The borrower's report (PDF or DOCX)

## How the score is calculated

```
Each page of the borrower's report
        │
        ▼
AI scores each KPI found on that page (0–100)
        │
        ▼
Each KPI keeps its best score from any page (not found = 0)
        │
        ▼
Category score = total of best scores ÷ (number of KPIs × 100) × 100
        │
        ▼
Overall score = weights for the loan type (see table)
        │
        ▼
Grade + lending recommendation
```

Steps 1 to 4 are the same as the ESG calculator.

**Step 1. Score each KPI on every page.** The AI reads each page and scores every KPI it finds, using this guide:

| Score | What the page shows |
|---|---|
| 0 | The KPI is not addressed |
| 1–30 | Only mentioned, no detail |
| 31–60 | A policy or commitment is described |
| 61–80 | Specific actions or programmes are described |
| 81–100 | Measured data, or targets with progress |

**Step 2. Keep the best score for each KPI.** If a KPI is found on several pages, its highest score counts. A KPI found nowhere scores 0.

**Step 3. Work out each category score.** Add the best scores of all the category's KPIs and divide by the maximum possible (number of KPIs × 100). The result is a percentage from 0 to 100.

**Step 4. Page score (for reference only).** A page's score is the average of the KPI scores found on that page. It explains the page but is **not** used in the final score.

**Step 5. Work out the overall score with the loan-type weights.**

| Loan type | Environment | Social | Governance |
|---|---|---|---|
| Working Capital, Term Loan, Gold Loan (Corporate), Lease Finance, Bridge Finance | 20% | 30% | 50% |
| MSME Loan | 25% | 30% | 45% |
| Machinery Loan, Equipment Finance | 45% | 25% | 30% |
| Project Finance, Infrastructure Finance | 45% | 25% | 30% |
| Construction Finance | 45% | 30% | 25% |
| Renewable Energy Loan | 50% | 20% | 30% |
| Agriculture Loan | 50% | 25% | 25% |
| Commercial Vehicle Loan | 35% | 25% | 40% |
| Home Loan (Builder), Real Estate Finance | 40% | 25% | 35% |
| Trade Finance, Supply Chain Finance | 20% | 25% | 55% |
| Export Finance | 25% | 30% | 45% |
| Venture Debt | 15% | 35% | 50% |

**Step 6. Give the grade and the lending recommendation.** Decimals are dropped before grading (79.9 counts as 79), the same as the ESG calculator.

| Overall score | Grade | Recommendation |
|---|---|---|
| Above 90 | A+ Outstanding | Favorable: low ESG credit risk |
| 80–90 | A Excellent | Favorable: low ESG credit risk |
| 71–79 | B+ Very Good | Moderate: lend with standard ESG conditions |
| 61–70 | B Good | Moderate: lend with standard ESG conditions |
| 40–60 | C Average | Caution: enhanced ESG due diligence recommended |
| Below 40 | D Below Average | High risk: detailed review before lending |

The KPI lists are the same as the ESG calculator: Environment 18, Social 18 and Governance 17.

## Example

To keep the example short, Environment has 10 KPIs here.

**Step 1. Scores found on the pages**

| Page | KPIs found (score) |
|---|---|
| 1 | KPI 4 (32) |
| 2 | KPI 8 (32), KPI 9 (80) |
| 7 | KPI 4 (50), KPI 9 (65) |

**Step 2. Best score per KPI**

| KPI | Best score | Why |
|---|---|---|
| KPI 4 | 50 | Page 7 (50) is higher than page 1 (32) |
| KPI 8 | 32 | Only found on page 2 |
| KPI 9 | 80 | Page 2 (80) is higher than page 7 (65) |
| The other 7 KPIs | 0 | Not found anywhere |

**Step 3. Environment score**

(50 + 32 + 80) ÷ (10 × 100) × 100 = **16.2**

Social and Governance are worked out the same way. Say Social = 55 and Governance = 70.

**Step 5 and 6. Overall score, grade and recommendation: same report, two loan types**

| | Working Capital (20 / 30 / 50) | Agriculture Loan (50 / 25 / 25) |
|---|---|---|
| Calculation | 0.20 × 16.2 + 0.30 × 55 + 0.50 × 70 | 0.50 × 16.2 + 0.25 × 55 + 0.25 × 70 |
| Overall | 3.24 + 16.50 + 35.00 = **54.74** | 8.10 + 13.75 + 17.50 = **39.35** |
| Grade | **C (Average)** | **D (Below Average)** |
| Recommendation | Caution: enhanced ESG due diligence | High risk: detailed review before lending |

The weak Environment score (16.2) hurts much more for an Agriculture Loan, where Environment counts for 50%.

## Why we score KPIs, not pages

We use the same method as the ESG calculator (Option A): each KPI keeps its best score, and the category score is built from all the KPIs. We do **not** average the page scores (Option B).

| Situation | KPI scores (chosen) | Average of pages (not chosen) |
|---|---|---|
| Report covers only 3 of 10 KPIs | Low score, because 7 missing KPIs count as 0 | High score, because missing KPIs are ignored |
| Borrower repeats the same policy on 10 pages | No change | Score goes up |
| Report adds 50 pages of general content | No change | Score changes |
| Credit committee asks "why this score?" | We show each KPI, its score and its page | We can only say "average of the pages" |

For a lender this matters even more: a borrower should not get a better risk grade just by sending a longer report.

## What the report also includes

One more AI step reads the report and writes:

- the top 5 ESG risks and the top 5 improvements
- a short climate risk summary and a governance summary
- key numbers: employees, women %, attrition, complaints, CSR

These help the credit team but do **not** change the score.

## What the CSV export shows

**Part 1. One row per page and category:** the page, category, reason, the KPIs found with their scores (for example "KPI 8 (32); KPI 9 (80)") and the page score (56).

**Part 2. Summary at the end of the file:** every KPI's best score and the pages it was found on, each category total, and the overall score with the loan-type weights and the grade.

## Questions and answers

**1. Is the BFSI score calculated the same way as the ESG score?**
Yes, for Environment, Social and Governance. Each KPI is scored 0–100, keeps its best score, and missing KPIs count as 0. The same report gets the same E, S and G scores in both calculators.

**2. What is different from the ESG calculator?**
Only the weights for the overall score. The ESG calculator always uses 35% E + 30% S + 35% G. The BFSI calculator uses the weights for the loan type.

**3. Why do the weights depend on the loan type?**
Different loans carry different ESG risks. A Renewable Energy or Agriculture loan depends heavily on environmental factors, while a Trade Finance loan depends more on governance.

**4. If there are 10 KPIs and only 8 are found, what happens?**
The 2 missing KPIs score 0 and still count. The total is divided by all 10 KPIs.

**5. Which score counts when a KPI is found on several pages?**
The highest one.

**6. Is the overall score calculated from page scores?**
No. It comes only from each KPI's best score. Page scores only explain each page.

**7. Does the industry change the weights?**
No. Only the loan type decides the weights. The industry is recorded and shown in the report.

**8. Does the lending recommendation come from the grade?**
Yes. A+ and A are Favorable, B+ and B are Moderate, C is Caution, and D is High risk.

**9. Do the risks and improvements written by the AI change the score?**
No. They are extra information for the credit team.

**10. Can the scores be corrected by hand?**
Yes. An admin can change a KPI's score in the report editor. The category score, overall score, grade and recommendation update automatically, and "Reset" goes back to the AI's scores.

**11. What happens to BFSI reports that were already scored?**
They keep their current scores and grades. Only new or re-run analyses use this method.
