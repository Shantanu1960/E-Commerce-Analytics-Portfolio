"""
compute_kpis.py
----------------
Reads the cleaned `ecommerce` Postgres database (built by Project 1) and computes the
KPI set a leadership dashboard needs:
  - Monthly active customers (MAC)
  - Repeat-purchase / retention rate
  - Churn proxy (90-day inactivity among customers old enough to qualify)
  - Revenue growth (MoM)
  - AOV trend

Exports clean, dashboard-ready CSVs to ./exports/. This script is the "regenerate the
numbers" half of the pipeline -- re-run it any time the underlying data changes, or
schedule it (see regenerate_kpis.py for a cron-friendly wrapper).
"""

import pandas as pd
import numpy as np
from sqlalchemy import create_engine

DB_URL = "postgresql+psycopg2://postgres:postgres@localhost:5432/ecommerce"
engine = create_engine(DB_URL)
OUT = "exports"

import os
os.makedirs(OUT, exist_ok=True)

# ---------------------------------------------------------------------------
# Pull the grain we need: one row per delivered order with its total value,
# customer, state, and month.
# ---------------------------------------------------------------------------
orders_fact = pd.read_sql("""
    SELECT
        o.order_id,
        o.order_purchase_timestamp,
        c.customer_unique_id,
        c.customer_state,
        SUM(oi.price) AS order_value
    FROM orders o
    JOIN customers c ON c.customer_id = o.customer_id
    JOIN order_items oi ON oi.order_id = o.order_id
    WHERE o.order_status = 'delivered'
    GROUP BY o.order_id, o.order_purchase_timestamp, c.customer_unique_id, c.customer_state
""", engine, parse_dates=["order_purchase_timestamp"])

orders_fact["month"] = orders_fact["order_purchase_timestamp"].dt.to_period("M").dt.to_timestamp()
max_date = orders_fact["order_purchase_timestamp"].max()

# ---------------------------------------------------------------------------
# 1. Monthly trend: revenue, orders, active customers, AOV, MoM growth
# ---------------------------------------------------------------------------
monthly = (
    orders_fact.groupby("month")
    .agg(
        revenue=("order_value", "sum"),
        orders=("order_id", "nunique"),
        active_customers=("customer_unique_id", "nunique"),
    )
    .reset_index()
    .sort_values("month")
)
monthly["aov"] = (monthly["revenue"] / monthly["orders"]).round(2)
monthly["revenue"] = monthly["revenue"].round(2)
monthly["mom_growth_pct"] = (monthly["revenue"].pct_change() * 100).round(1)

monthly.to_csv(f"{OUT}/monthly_trend.csv", index=False)

# ---------------------------------------------------------------------------
# 2. Customer retention / repeat-purchase rate (lifetime, by real person)
# ---------------------------------------------------------------------------
orders_per_person = orders_fact.groupby("customer_unique_id")["order_id"].nunique()
repeat_customers = (orders_per_person > 1).sum()
total_customers = orders_per_person.shape[0]
repeat_rate_pct = round(100 * repeat_customers / total_customers, 1)

# ---------------------------------------------------------------------------
# 3. Churn proxy: among customers whose FIRST order was >90 days before the
#    most recent date in the dataset (i.e. they've had a fair chance to come
#    back), what % have NOT ordered in the trailing 90 days?
# ---------------------------------------------------------------------------
first_order = orders_fact.groupby("customer_unique_id")["order_purchase_timestamp"].min()
last_order = orders_fact.groupby("customer_unique_id")["order_purchase_timestamp"].max()
eligible = first_order[first_order <= (max_date - pd.Timedelta(days=90))].index
churned = last_order.loc[eligible] < (max_date - pd.Timedelta(days=90))
churn_rate_pct = round(100 * churned.sum() / len(eligible), 1)

# ---------------------------------------------------------------------------
# 4. Category breakdown (drill-down page)
# ---------------------------------------------------------------------------
category_breakdown = pd.read_sql("""
    SELECT
        p.product_category_name AS category,
        ROUND(SUM(oi.price)::numeric, 2) AS revenue,
        COUNT(DISTINCT o.order_id) AS orders,
        ROUND(AVG(oi.price)::numeric, 2) AS avg_item_price
    FROM order_items oi
    JOIN orders o ON o.order_id = oi.order_id
    JOIN products p ON p.product_id = oi.product_id
    WHERE o.order_status = 'delivered'
    GROUP BY p.product_category_name
    ORDER BY revenue DESC
""", engine)
category_breakdown.to_csv(f"{OUT}/category_breakdown.csv", index=False)

# ---------------------------------------------------------------------------
# 5. Region breakdown (drill-down page)
# ---------------------------------------------------------------------------
region_breakdown = (
    orders_fact.groupby("customer_state")
    .agg(orders=("order_id", "nunique"), revenue=("order_value", "sum"))
    .reset_index()
)
region_breakdown["avg_order_value"] = (region_breakdown["revenue"] / region_breakdown["orders"]).round(2)
region_breakdown["revenue"] = region_breakdown["revenue"].round(2)
region_breakdown = region_breakdown.sort_values("revenue", ascending=False)
region_breakdown.to_csv(f"{OUT}/region_breakdown.csv", index=False)

# ---------------------------------------------------------------------------
# 6. Overview KPI summary (the 4-5 "cards" for the dashboard front page)
# ---------------------------------------------------------------------------
latest_month = monthly.iloc[-1]
prior_month = monthly.iloc[-2] if len(monthly) > 1 else None

kpi_summary = pd.DataFrame([{
    "metric": "total_revenue", "value": round(monthly["revenue"].sum(), 2)
}, {
    "metric": "total_orders", "value": int(monthly["orders"].sum())
}, {
    "metric": "total_customers", "value": int(total_customers)
}, {
    "metric": "repeat_purchase_rate_pct", "value": repeat_rate_pct
}, {
    "metric": "churn_rate_pct_90d", "value": churn_rate_pct
}, {
    "metric": "overall_aov", "value": round(monthly["revenue"].sum() / monthly["orders"].sum(), 2)
}, {
    "metric": "latest_month", "value": str(latest_month["month"].date())
}, {
    "metric": "latest_month_revenue", "value": latest_month["revenue"]
}, {
    "metric": "latest_month_mom_growth_pct", "value": latest_month["mom_growth_pct"]
}, {
    "metric": "latest_month_active_customers", "value": int(latest_month["active_customers"])
}])
kpi_summary.to_csv(f"{OUT}/kpi_summary.csv", index=False)

# ---------------------------------------------------------------------------
print("KPI exports written to ./exports/:")
for f in ["monthly_trend.csv", "kpi_summary.csv", "category_breakdown.csv", "region_breakdown.csv"]:
    print(f"  {f}")

print("\n--- KPI Summary ---")
print(kpi_summary.to_string(index=False))
print("\n--- Monthly trend (last 6 months) ---")
print(monthly.tail(6).to_string(index=False))
