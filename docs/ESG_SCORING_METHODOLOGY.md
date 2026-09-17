# ESG Calculator: Scoring Methodology

## Summary

The ESG score measures **how good a company's performance is** on a fixed list of KPIs,
read from the report it uploads.

- Every page is first placed in a category: Environment, Social, Governance, or more than one.
- Only that category's KPIs are then matched against the page.
- Each KPI found is scored from 0 to 100 on **how good the performance is** — not on how much
  detail the report gives.
- A KPI that is only mentioned, only promised, or too vague to judge scores **0**.
- A KPI that is not found anywhere scores **0** and still counts.
- Each category score is the **average of all its KPI scores**.
- The final score is **35% Environment + 30% Social + 35% Governance**.

The BFSI calculator uses the same method with its own weights — see
`BFSI_SCORING_METHODOLOGY.md`.

## How the score is calculated

```
Each page of the report
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
Step 4: overall = 35% Environment + 30% Social + 35% Governance
        │
        ▼
Step 5: grade
```

### Step 0. Place the page in a category

One AI call per page asks which categories the page has real content about.

| Answer | What happens |
|---|---|
| Environment | only the 125 Environment KPIs are matched against that page |
| Environment + Social | both sets are matched; Governance is not asked about that page |
| (none) | the page is not scored at all — a cover page, an index, a photo caption, a purely financial table |

A page can belong to more than one category. If the call fails, the page is scored for all
three categories, so no page is ever lost because a classification failed.

### Step 1. Score each KPI found on the page

The AI reads the page and scores only the KPIs that page says something about:

| Score | What the page shows about that KPI |
|---|---|
| **0** | You cannot judge the performance: only named, listed or mentioned; only promised, planned or pending; or too little said to tell whether it is good |
| **1–20** | Poor: fines, penalties, lawsuits, incidents, accidents, a worsening trend, an admitted failure |
| **21–40** | Weak: something is being done, but early, partial or thin, with no result |
| **41–60** | Moderate: real actions or programmes in place, but no measured result |
| **61–80** | Good: measured results, or real progress against a target |
| **81–100** | Strong: targets met, a measured improvement, independent assurance or certification |

The judgement is **how good the performance is**, never how much detail is written. A page
with a long paragraph that says nothing about performance scores 0 for that KPI.

### Step 2. Each KPI keeps its best score

If a KPI appears on several pages, its **highest** score counts — with one exception:

> **If any page showed poor performance (1–20) for that KPI, the KPI is held down to 20**,
> however good another page looks.

So a fine on page 30 cannot be cancelled out by a nice paragraph on page 4. A KPI found
nowhere scores 0. The report and the CSV mark a held-down KPI as *capped at 20 (poor
performance found)*.

### Step 3. Category score

The average of **all** that category's KPI scores, with missing KPIs counted as 0:

```
Category score = total of the KPI scores ÷ (number of KPIs × 100) × 100
```

Which is the same thing as the average, on a 0–100 scale. Example: Environment has 125
KPIs. If three are scored 80, 70 and 60 and the other 122 are not found:

(80 + 70 + 60) ÷ (125 × 100) × 100 = 210 ÷ 12500 × 100 = **1.68**

That number is low on purpose. It says the report evidenced good performance on 3 of 125
Environment metrics. The report also prints how many KPIs were found, so the score and the
coverage are both visible.

### Step 4. Overall score

Overall = 35% Environment + 30% Social + 35% Governance.

### Step 5. Grade

| Overall score | Grade |
|---|---|
| Above 90 | A+ Outstanding |
| 80–90 | A Excellent |
| 71–79 | B+ Very Good |
| 61–70 | B Good |
| 40–60 | C Average |
| Below 40 | D Below Average |

The score is truncated before grading, so 79.9 is read as 79.

## Where the KPIs come from

The KPIs come from our ESG metrics sheet, stored in the database (`esg_kpis`). Each metric
sits in a group:

**Pillar → Sub Pillar → Sub Pillar 1 → Metric**, for example **E → Water → Water II → Water withdrawn**.

| Pillar | Metrics scored | Sub Pillars |
|---|---|---|
| Environment | 125 | Emission, Environmental Management, Environmental Solution, Environmental Stewardship, Resource Use, Waste, Water |
| Social | 129 | Community Relations, Diversity, Compensation, Employment Quality, Human Rights, Labour Rights, OHS, Product Access, Product Quality & Safety, Training & Development |
| Governance | 82 | Business Ethics, Compensation, Diversity, Governance, Transparency |

- The AI gets each metric **with its Sub Pillar and Sub Pillar 1**, so it understands what a
  short name like "Company loans" or "Auditor Opinion" is about.
- 5 "Meta" rows (Number of Employees, Current auditor, Reporting boundary, CSO and Investor
  Relations contact details) are company facts, not scored.

## Worked example

To keep it short, Environment has 10 KPIs here.

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
| 7 | KPI 9 | 15 | an environmental fine was paid |

**Step 2. Best score per KPI**

| KPI | Score | Why |
|---|---|---|
| KPI 4 | 55 | page 7 (55) beats page 1 (0) |
| KPI 8 | 45 | only found on page 2 |
| KPI 9 | **20** | page 2 scored 75, but page 7 showed poor performance (15) → capped at 20 |
| The other 7 KPIs | 0 | not found anywhere |

**Step 3. Environment score**

(55 + 45 + 20) ÷ (10 × 100) × 100 = 120 ÷ 1000 × 100 = **12.0**

**Step 4. Overall score**

Say Social = 22 and Governance = 30.

Overall = 0.35 × 12.0 + 0.30 × 22 + 0.35 × 30 = 4.20 + 6.60 + 10.50 = **21.30**

**Step 5. Grade** — 21.30 is below 40, so the grade is **D (Below Average)**.

## What the CSV export shows

**Part 1. One row per page and category** — only the categories that page belongs to:

| Page | Category | Reason | KPIs found (score) | Page score |
|---|---|---|---|---|
| 1 | Environment | … | KPI 4 (0) | 0 |
| 2 | Environment | … | KPI 9 (75); KPI 8 (45) | 60 |
| 2 | Social | … | KPI 3 (40) | 40 |
| 7 | Environment | … | KPI 4 (55); KPI 9 (15) | 35 |

The page score is the average of the KPI scores on that page. It explains that page only
and is **not** used in the final score.

**Part 2. Summary at the end of the file**

| Category | KPI | Best Score | Found on Pages |
|---|---|---|---|
| Environment | KPI 4 | 55 | 1, 7 |
| Environment | KPI 8 | 45 | 2 |
| Environment | KPI 9 | 20 | 2, 7 (capped at 20: poor performance found) |
| Environment | KPI 1 | 0 | – |
| Environment total | | 12.0 | |
| Overall | 35% Environment + 30% Social + 35% Governance | 21.30 | Grade D |

## Questions and answers

**1. On what basis is a KPI scored?**
On how good the company's performance is, judged from the page's own content. Not on how
much detail the report gives.

**2. The report mentions a KPI but says nothing about performance. What score?**
**0.** The same for a promise, a plan, or anything pending — if the performance cannot be
judged, the score is 0.

**3. What if the performance is bad?**
1–20, depending on how bad. Bad performance scores something, so the report can tell "we
paid fines" apart from "we said nothing" — and it caps that KPI at 20 everywhere.

**4. Why are the same KPI's pages sometimes capped?**
Because good news does not erase bad news. If any page shows poor performance for a KPI,
that KPI cannot score above 20 no matter what another page says.

**5. If a category has 125 KPIs and only 3 are found, what happens?**
The other 122 score 0 and still count. The category score is the average of all 125, so it
will be low. That is the honest answer: the report did not evidence those metrics.

**6. Why did the scores drop compared with the old reports?**
The old reports were scored a different way: every KPI the document touched anywhere
counted as fully proved (100) or half proved (50), and the list held only 18 KPIs per
category. That rewarded long reports and could never be lowered by bad news. Old reports
keep their old scores; only new or re-run analyses use this method.

**7. Which pages are scored for which category?**
Step 0 decides. A page about water and workers is scored for Environment and Social; a page
with no ESG content is not scored at all.

**8. Is the overall score calculated from the page scores?**
No. Only from the KPI scores. Page scores are shown to explain each page.

**9. How are Environment, Social and Governance combined?**
Overall = **35% Environment + 30% Social + 35% Governance**. BFSI uses the loan type's
weights instead.

**10. How much is one KPI worth?**
All KPIs in a category count equally. One Environment metric moved from 0 to 100 adds
100 ÷ 125 = 0.8 to the Environment score, and 0.8 × 35% = 0.28 to the overall score.

**11. Can the scores be corrected by hand?**
Yes. An admin can change a KPI's score in the report editor. The category score, overall
score and grade update automatically, an edited KPI is no longer capped, and "Reset" goes
back to the AI's scores.

**12. What happens to reports that were already scored?**
They keep their current scores. Only new or re-run analyses use this method.
