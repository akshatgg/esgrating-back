# BFSI Calculator: Scoring Methodology

## Summary

The BFSI calculator helps a lender judge the ESG risk of a borrower before giving a loan.
It scores the borrower's report **exactly like the ESG calculator**:

- every page is first placed in a category (Environment, Social, Governance, or more than one);
- only that category's KPIs are then matched against the page;
- each KPI found is scored 0 to 100 on **how good the performance is**, not on how much detail the report gives;
- a KPI only mentioned, only promised, or too vague to judge scores **0**;
- a KPI not found anywhere scores **0** and still counts;
- each category score is the **average of all its KPI scores**.

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
Step 0: which category is this page? (E, S, G, or several; none = page skipped)
        │
        ▼
Step 1: for those categories only, score each KPI the page shows (0–100, how good)
        │
        ▼
Step 2: each KPI keeps its best score (not found = 0; poor performance caps it at 20)
        │
        ▼
Step 3: category score = average of all that category's KPI scores
        │
        ▼
Step 4: overall score = weights for the loan type (see table)
        │
        ▼
Grade + lending recommendation
```

Steps 0 to 3 are the same as the ESG calculator.

**Step 0. Place the page in a category.** One AI call per page asks which categories the page has real content about. Only those categories' KPIs are matched against it. A page with no ESG content — a cover page, an index, a purely financial table — is not scored at all. A page can belong to more than one category.

**Step 1. Score each KPI found on the page.** The AI scores only the KPIs that page says something about:

| Score | What the page shows about that KPI |
|---|---|
| **0** | You cannot judge the performance: only named or mentioned; only promised, planned or pending; or too little said to tell whether it is good |
| **1–20** | Poor: fines, penalties, lawsuits, incidents, accidents, a worsening trend, an admitted failure |
| **21–40** | Weak: something is being done, but early, partial or thin, with no result |
| **41–60** | Moderate: real actions or programmes in place, but no measured result |
| **61–80** | Good: measured results, or real progress against a target |
| **81–100** | Strong: targets met, a measured improvement, independent assurance or certification |

The judgement is **how good the performance is**, never how much detail is written.

**Step 2. Keep the best score for each KPI.** If a KPI is found on several pages, its highest score counts — except that **if any page showed poor performance (1–20), the KPI is held down to 20**, however good another page looks. For a lender this is the point: a penalty on page 30 is not cancelled out by a good paragraph on page 4. A KPI found nowhere scores 0.

**Step 3. Work out each category score.** The average of **all** that category's KPI scores, with missing KPIs counted as 0 — the total of the scores ÷ (number of KPIs × 100) × 100.

**Page score (for reference only).** A page's score is the average of the KPI scores found on that page. It explains the page but is **not** used in the final score.

**Step 4. Work out the overall score with the loan-type weights.**

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

**Step 5. Give the grade and the lending recommendation.** Decimals are dropped before grading (79.9 counts as 79), the same as the ESG calculator.

| Overall score | Grade | Recommendation |
|---|---|---|
| Above 90 | A+ Outstanding | Favorable: low ESG credit risk |
| 80–90 | A Excellent | Favorable: low ESG credit risk |
| 71–79 | B+ Very Good | Moderate: lend with standard ESG conditions |
| 61–70 | B Good | Moderate: lend with standard ESG conditions |
| 40–60 | C Average | Caution: enhanced ESG due diligence recommended |
| Below 40 | D Below Average | High risk: detailed review before lending |

The KPIs are the same as the ESG calculator: our ESG metrics sheet, stored in the database (`esg_kpis`). Environment has 125 metrics, Social 129 and Governance 82, each grouped as **Pillar → Sub Pillar → Sub Pillar 1 → Metric** (for example E → Water → Water II → Water withdrawn). The AI gets each metric with its Sub Pillar and Sub Pillar 1, so it understands what the metric is about. The 5 "Meta" rows (company facts such as auditor name and contact details) are not scored.

## Example

To keep the example short, Environment has 10 KPIs here.

**Step 0. Categories of each page**

| Page | Categories |
|---|---|
| 1 | Environment |
| 2 | Environment, Social |
| 3 | (none — index page, skipped) |
| 7 | Environment |

**Step 1. Environment KPI scores on those pages**

| Page | KPI | Score | Why |
|---|---|---|---|
| 1 | KPI 4 | 0 | the policy is named, nothing shown about performance |
| 2 | KPI 8 | 45 | a programme is running, no results given |
| 2 | KPI 9 | 75 | emissions down 12%, measured |
| 7 | KPI 4 | 55 | the same policy, now with actions described |
| 7 | KPI 9 | 15 | an environmental penalty was paid |

**Step 2. Best score per KPI**

| KPI | Score | Why |
|---|---|---|
| KPI 4 | 55 | page 7 (55) beats page 1 (0) |
| KPI 8 | 45 | only found on page 2 |
| KPI 9 | **20** | page 2 scored 75, but page 7 showed poor performance (15) → capped at 20 |
| The other 7 KPIs | 0 | not found anywhere |

**Step 3. Environment score**

(55 + 45 + 20) ÷ (10 × 100) × 100 = **12.0**

Social and Governance are worked out the same way. Say Social = 22 and Governance = 30.

**Steps 4 and 5. Overall score, grade and recommendation: same report, two loan types**

| | Working Capital (20 / 30 / 50) | Agriculture Loan (50 / 25 / 25) |
|---|---|---|
| Calculation | 0.20 × 12.0 + 0.30 × 22 + 0.50 × 30 | 0.50 × 12.0 + 0.25 × 22 + 0.25 × 30 |
| Overall | 2.40 + 6.60 + 15.00 = **24.00** | 6.00 + 5.50 + 7.50 = **19.00** |
| Grade | **D (Below Average)** | **D (Below Average)** |
| Recommendation | High risk: detailed review before lending | High risk: detailed review before lending |

The weak Environment score (12.0) hurts more for an Agriculture Loan, where Environment counts for 50%.

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
Yes, for Environment, Social and Governance. The page is placed in a category first, each KPI found is scored 0–100 on how good the performance is, a KPI keeps its best score (capped at 20 if any page showed poor performance), and missing KPIs count as 0. The same report gets the same E, S and G scores in both calculators.

**2. What is different from the ESG calculator?**
Only the weights for the overall score. The ESG calculator always uses 35% E + 30% S + 35% G. The BFSI calculator uses the weights for the loan type.

**3. Why do the weights depend on the loan type?**
Different loans carry different ESG risks. A Renewable Energy or Agriculture loan depends heavily on environmental factors, while a Trade Finance loan depends more on governance.

**4. If there are 10 KPIs and only 8 are found, what happens?**
The 2 missing KPIs score 0 and still count. The total is divided by all 10 KPIs.

**5. Which score counts when a KPI is found on several pages?**
The highest one — unless any page showed poor performance (1–20) for that KPI, in which case it is held down to 20. Good news does not erase a penalty.

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

**12. The borrower only mentions a policy. Does that earn marks?**
No — 0. A lender cannot judge risk from the fact that a document names something. Marks start once the report shows what the borrower actually does and how well it works.
