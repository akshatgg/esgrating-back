# ESG Calculator: Scoring Methodology

## Summary

The ESG score measures how well a company's report proves a fixed list of KPIs.
Each KPI is scored from 0 to 100, and a KPI that is missing scores 0.
The final score is **35% Environment + 30% Social + 35% Governance**.

This method is for the ESG calculator only. The BFSI calculator does not change.

## How the score is calculated

```
Each page of the report
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
Overall score = 35% Environment + 30% Social + 35% Governance
        │
        ▼
Grade
```

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

**Step 4. Work out the overall score.** Overall = 35% Environment + 30% Social + 35% Governance.

**Step 5. Give the grade.**

| Overall score | Grade |
|---|---|
| Above 90 | A+ Outstanding |
| 80–90 | A Excellent |
| 71–79 | B+ Very Good |
| 61–70 | B Good |
| 40–60 | C Average |
| Below 40 | D Below Average |

## Where the KPIs come from

The KPIs come from our ESG metrics sheet, stored in the database (`esg_kpis`). Each metric sits in a group:

**Pillar → Sub Pillar → Sub Pillar 1 → Metric**, for example **E → Water → Water II → Water withdrawn**.

| Pillar | Metrics scored | Sub Pillars |
|---|---|---|
| Environment | 125 | Emission, Environmental Management, Environmental Solution, Environmental Stewardship, Resource Use, Waste, Water |
| Social | 129 | Community Relations, Diversity, Compensation, Employment Quality, Human Rights, Labour Rights, OHS, Product Access, Product Quality & Safety, Training & Development |
| Governance | 82 | Business Ethics, Compensation, Diversity, Governance, Transparency |

- The AI gets each metric **with its Sub Pillar and Sub Pillar 1**, so it understands what a short name like "Company loans" or "Auditor Opinion" is about.
- 5 "Meta" rows (Number of Employees, Current auditor, Reporting boundary, CSO and Investor Relations contact details) are company facts, not scored.
- The calculation below stays the same; only the list of KPIs is bigger.

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

(50 + 32 + 80) ÷ (10 × 100) × 100 = 162 ÷ 1000 × 100 = **16.2**

**Step 4. Overall score**

Social and Governance are worked out the same way. Say Social = 55 and Governance = 70.

Overall = 0.35 × 16.2 + 0.30 × 55 + 0.35 × 70 = 5.67 + 16.50 + 24.50 = **46.67**

**Step 5. Grade**

46.67 is between 40 and 60, so the grade is **C (Average)**.

## Why we score KPIs, not pages

We looked at two options and chose **Option A**.

- **Option A (chosen):** each KPI keeps its best score, and the category score is built from all the KPIs.
- **Option B (not chosen):** the category score is the average of the page scores.

With the example above, Option A gives Environment **16.2**. Option B gives (32 + 56 + 57.5) ÷ 3 = **48.5**.

| Situation | Option A | Option B |
|---|---|---|
| Report covers only 3 of 10 KPIs | Low score, because 7 missing KPIs count as 0 | High score, because missing KPIs are ignored |
| Company repeats the same policy on 10 pages | No change, because the best score counts once | Score goes up |
| Report adds 50 pages of general content | No change | Score changes |
| Client asks "why this score?" | We show each KPI, its score and its page | We can only say "average of the pages" |

Why Option A is better:

- **It follows professional practice.** ESG ratings and India's BRSR framework check a fixed set of indicators. The length of the report doesn't count.
- **It is fair.** A longer report or repeated text cannot raise the score.
- **It shows the gaps.** Missing KPIs are listed, so the company knows what to disclose next.
- **It can be checked.** Every number traces back to a KPI and a page.

## What the CSV export shows

**Part 1. One row per page and category**

| Page | Category | Reason | KPIs found (score) | Page score |
|---|---|---|---|---|
| 1 | Environment | … | KPI 4 (32) | 32 |
| 2 | Environment | … | KPI 8 (32); KPI 9 (80) | 56 |
| 7 | Environment | … | KPI 4 (50); KPI 9 (65) | 57.5 |

The page score is the average of the KPI scores on that page, for example (32 + 80) ÷ 2 = 56.
It only explains that page. It is **not** used in the final score.

**Part 2. Summary at the end of the file**

| Category | KPI | Best score | Found on pages |
|---|---|---|---|
| Environment | KPI 4 | 50 | 1, 7 |
| Environment | KPI 8 | 32 | 2 |
| Environment | KPI 9 | 80 | 2, 7 |
| Environment | KPI 1 | 0 | – |
| Environment total | | 16.2 | |
| Overall | | 46.67 (Grade C) | |

## Questions and answers

**1. On what basis is the ESG score calculated?**
Only on the KPIs. Each KPI is scored from 0 to 100 based on what the report proves about it.

**2. If there are 10 KPIs and only 8 are found in the report, what happens?**
The 2 missing KPIs score 0 and still count. The total is divided by all 10 KPIs, not by 8. For example, if the 8 found KPIs add up to 560: 560 ÷ 1000 × 100 = **56**.

**3. Can a KPI get any score, like 20, 25 or 32?**
Yes. Each KPI gets a score from 0 to 100 using the scoring guide above. (Before this change, a KPI could only get 100, 50 or 0.)

**4. The same KPI is found on different pages with different scores. Which one counts?**
The highest one. If KPI 9 scores 80 on page 2 and 65 on page 7, KPI 9 = **80**.

**5. How is the score for each page given (for example 43 or 23)?**
The page score is the average of the KPI scores found on that page. Page 2 has KPI 8 (32) and KPI 9 (80), so the page score is **56**. If a page has no KPIs, its score is 0.

**6. Is the overall score calculated from the page scores?**
No. The overall score comes only from each KPI's best score. Page scores are shown in the CSV to explain each page, but they don't affect the rating.

**7. Which option is better and more professional: KPI scores (A) or page scores (B)?**
Option A. See "Why we score KPIs, not pages" above.

**8. How are Environment, Social and Governance combined?**
Overall = **35% Environment + 30% Social + 35% Governance**. This applies to the ESG calculator only, not BFSI.

**9. How much is one KPI worth?**
All KPIs in a category count equally. One Environment metric moved from 0 to 100 adds 100 ÷ 125 = 0.8 to the Environment score, and 0.8 × 35% = 0.28 to the overall score.

**10. What does the CSV show?**
Each page with its KPIs and their scores, then each KPI's best score, the category totals and the overall score.

**11. Can the scores be corrected by hand?**
Yes. An admin can change a KPI's score in the report editor. The category score, overall score and grade update automatically, and "Reset" goes back to the AI's scores.

**12. What happens to reports that were already scored?**
They keep their current scores. Only new or re-run analyses use this method.
