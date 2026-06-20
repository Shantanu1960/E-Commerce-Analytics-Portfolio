# Project 2 — KPI Dashboard for Non-Technical Stakeholders

Turns the cleaned order data from Project 1 into a leadership-ready read: KPIs, an interactive dashboard, and a
one-page insight memo.

## A note on tooling

available, and no Microsoft/Google service auth). Instead of a Power BI `.pbix`, the dashboard here is a
**self-contained interactive HTML file** (`dashboard.html`) — open it in any browser, no server or install
needed. The KPI logic and the exported CSVs (`exports/*.csv`) are the same numbers you'd plug into Power BI or
Looker Studio directly if you have access to them; the CSVs are formatted for that (tidy, one grain per file,
no merged headers).

## Files

```
compute_kpis.py            # one-shot: reads Postgres, computes KPIs, writes exports/*.csv
regenerate_kpis.py         # schedulable wrapper (cron-friendly) — recomputes CSVs + rebuilds dashboard.html
dashboard_template.html    # dashboard shell (placeholder __DATA_JSON__)
dashboard.html             # the actual dashboard, data already injected — open this one
insight_memo.docx          # one-page exec memo: 3 findings + 1 recommendation each
exports/
  monthly_trend.csv          # month, revenue, orders, active_customers, aov, mom_growth_pct
  kpi_summary.csv            # the 5 headline numbers shown as cards on the dashboard
  category_breakdown.csv     # revenue/orders/avg item price by category
  region_breakdown.csv       # revenue/orders/AOV by state
  dashboard_data.json        # all of the above, bundled (what dashboard.html reads)
```

## Run order

```bash
pip install pandas numpy sqlalchemy psycopg2-binary
python3 compute_kpis.py          # needs Project 1's Postgres `ecommerce` DB already loaded
python3 regenerate_kpis.py       # also rebuilds dashboard.html from the template
open dashboard.html              # or just double-click it
```

## KPIs computed

| KPI | Definition | Why it's on the dashboard |
|---|---|---|
| Monthly revenue & orders | Sum of delivered order line items / count, by month | The headline trend line |
| Active customers (monthly) | Distinct real customers (by `customer_unique_id`) with a delivered order that month | Are we keeping the funnel full, not just riding a few big orders |
| AOV trend | Revenue ÷ orders, by month | Catches "more orders but each one is smaller" type shifts that revenue alone hides |
| Repeat-purchase rate | % of customers (lifetime) with more than one order | Core retention signal — see methodology note below |
| Churn proxy (90-day) | Among customers whose *first* order was 90+ days before the latest date in the data, % with no order in the trailing 90 days | A leading indicator of customers about to be lost, not just a lagging "they're gone" count |
| MoM revenue growth % | (this month − last month) ÷ last month | Pairs with the trend line so growth/decline doesn't have to be eyeballed off a chart |

**Methodology note that matters:** retention and churn are grouped by `customer_unique_id` (the real person),
not `customer_id` (issued fresh per order in this schema). Grouping by the wrong column silently understates
retention — see Project 1's README for the full explanation.

**Honesty note on the churn number specifically:** because this is synthetic data with order timing close to
uniformly random (not modeled on real customer lifecycle behavior), the ~77% churn-proxy figure is likely
overstated and shouldn't be quoted as a literal finding. The *calculation itself* — 90-day eligibility window,
grouped by real customer identity — is the right one to run once this pipeline points at live data; only the
number changes, not the method.

## Dashboard

Two views:
- **Overview** — 5 KPI cards, a revenue+AOV trend chart, an active-customers trend chart, and a "pulse strip" at
  the top (one bar per month, colored by whether that month grew or shrank vs. the prior one — hover for exact
  numbers).
- **Drill-down** — revenue by category (chart + table) and revenue/AOV by state (table), for the follow-up
  questions a stakeholder usually asks right after the headline numbers.

## Insight memo

`insight_memo.docx` — one page, three findings, each with a one-line "what the data shows" and one concrete
recommendation. Written for a leadership audience: no SQL, no jargon, plain dollar figures and percentages.

## Automation (the "regenerate reports on a schedule" stretch goal)

`regenerate_kpis.py` is the schedulable entry point: it re-runs the KPI computation, re-bundles the CSVs into
`dashboard_data.json`, and rebuilds `dashboard.html` from the template — so the dashboard reflects whatever is
currently in Postgres without anyone re-running a notebook by hand. It logs to `logs/regenerate.log` so failures
show up in monitoring rather than silently leaving a stale dashboard live. Example cron entry is in the script's
docstring (daily at 6am).
