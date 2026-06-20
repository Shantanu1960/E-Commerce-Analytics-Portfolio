#!/usr/bin/env python3
"""
regenerate_kpis.py
-------------------
Schedulable entry point that regenerates the KPI exports and rebuilds the
dashboard HTML from the latest data in Postgres. Designed to be run by cron
(or any scheduler) so the "leadership dashboard" never goes stale without
someone manually re-running notebooks.

Usage:
    python3 regenerate_kpis.py

Example cron entry (regenerate every morning at 6am):
    0 6 * * * cd /path/to/project2 && /usr/bin/python3 regenerate_kpis.py >> logs/regenerate.log 2>&1

What it does:
    1. Runs compute_kpis.py's logic to refresh exports/*.csv
    2. Re-bundles those CSVs into exports/dashboard_data.json
    3. Re-injects that JSON into dashboard_template.html -> dashboard.html
    4. Logs a timestamped summary line so failures/successes show up in monitoring
"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import create_engine

BASE = Path(__file__).resolve().parent
LOG_DIR = BASE / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "regenerate.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("regenerate_kpis")

DB_URL = "postgresql+psycopg2://postgres:postgres@localhost:5432/ecommerce"


def compute_and_export():
    engine = create_engine(DB_URL)
    out = BASE / "exports"
    out.mkdir(exist_ok=True)

    orders_fact = pd.read_sql(
        """
        SELECT o.order_id, o.order_purchase_timestamp, c.customer_unique_id,
               c.customer_state, SUM(oi.price) AS order_value
        FROM orders o
        JOIN customers c ON c.customer_id = o.customer_id
        JOIN order_items oi ON oi.order_id = o.order_id
        WHERE o.order_status = 'delivered'
        GROUP BY o.order_id, o.order_purchase_timestamp, c.customer_unique_id, c.customer_state
        """,
        engine, parse_dates=["order_purchase_timestamp"],
    )
    orders_fact["month"] = orders_fact["order_purchase_timestamp"].dt.to_period("M").dt.to_timestamp()
    max_date = orders_fact["order_purchase_timestamp"].max()

    monthly = (
        orders_fact.groupby("month")
        .agg(revenue=("order_value", "sum"), orders=("order_id", "nunique"),
             active_customers=("customer_unique_id", "nunique"))
        .reset_index().sort_values("month")
    )
    monthly["aov"] = (monthly["revenue"] / monthly["orders"]).round(2)
    monthly["revenue"] = monthly["revenue"].round(2)
    monthly["mom_growth_pct"] = (monthly["revenue"].pct_change() * 100).round(1)
    monthly.to_csv(out / "monthly_trend.csv", index=False)

    orders_per_person = orders_fact.groupby("customer_unique_id")["order_id"].nunique()
    repeat_rate_pct = round(100 * (orders_per_person > 1).sum() / len(orders_per_person), 1)

    first_order = orders_fact.groupby("customer_unique_id")["order_purchase_timestamp"].min()
    last_order = orders_fact.groupby("customer_unique_id")["order_purchase_timestamp"].max()
    eligible = first_order[first_order <= (max_date - pd.Timedelta(days=90))].index
    churned = last_order.loc[eligible] < (max_date - pd.Timedelta(days=90))
    churn_rate_pct = round(100 * churned.sum() / len(eligible), 1) if len(eligible) else None

    category_breakdown = pd.read_sql(
        """
        SELECT p.product_category_name AS category, ROUND(SUM(oi.price)::numeric, 2) AS revenue,
               COUNT(DISTINCT o.order_id) AS orders, ROUND(AVG(oi.price)::numeric, 2) AS avg_item_price
        FROM order_items oi
        JOIN orders o ON o.order_id = oi.order_id
        JOIN products p ON p.product_id = oi.product_id
        WHERE o.order_status = 'delivered'
        GROUP BY p.product_category_name ORDER BY revenue DESC
        """, engine,
    )
    category_breakdown.to_csv(out / "category_breakdown.csv", index=False)

    region_breakdown = (
        orders_fact.groupby("customer_state")
        .agg(orders=("order_id", "nunique"), revenue=("order_value", "sum")).reset_index()
    )
    region_breakdown["avg_order_value"] = (region_breakdown["revenue"] / region_breakdown["orders"]).round(2)
    region_breakdown["revenue"] = region_breakdown["revenue"].round(2)
    region_breakdown = region_breakdown.sort_values("revenue", ascending=False)
    region_breakdown.to_csv(out / "region_breakdown.csv", index=False)

    latest = monthly.iloc[-1]
    kpi_summary = pd.DataFrame([
        {"metric": "total_revenue", "value": round(monthly["revenue"].sum(), 2)},
        {"metric": "total_orders", "value": int(monthly["orders"].sum())},
        {"metric": "total_customers", "value": int(len(orders_per_person))},
        {"metric": "repeat_purchase_rate_pct", "value": repeat_rate_pct},
        {"metric": "churn_rate_pct_90d", "value": churn_rate_pct},
        {"metric": "overall_aov", "value": round(monthly["revenue"].sum() / monthly["orders"].sum(), 2)},
        {"metric": "latest_month", "value": str(latest["month"].date())},
        {"metric": "latest_month_revenue", "value": latest["revenue"]},
        {"metric": "latest_month_mom_growth_pct", "value": latest["mom_growth_pct"]},
        {"metric": "latest_month_active_customers", "value": int(latest["active_customers"])},
    ])
    kpi_summary.to_csv(out / "kpi_summary.csv", index=False)

    return monthly, kpi_summary, category_breakdown, region_breakdown


def rebuild_dashboard_json(monthly, kpi_summary, category_breakdown, region_breakdown):
    out = BASE / "exports"
    data = {
        "monthly": monthly.replace({np.nan: None}).to_dict("records"),
        "kpi": dict(zip(kpi_summary["metric"], kpi_summary["value"])),
        "category": category_breakdown.to_dict("records"),
        "region": region_breakdown.to_dict("records"),
    }
    (out / "dashboard_data.json").write_text(json.dumps(data, default=str))
    return data


def rebuild_dashboard_html(data_json_str):
    template_path = BASE / "dashboard_template.html"
    if not template_path.exists():
        log.warning("dashboard_template.html not found, skipping HTML rebuild")
        return
    tmpl = template_path.read_text()
    out_html = tmpl.replace("__DATA_JSON__", data_json_str)
    (BASE / "dashboard.html").write_text(out_html)


def main():
    started = datetime.now()
    log.info("Starting KPI regeneration")
    try:
        monthly, kpi_summary, category_breakdown, region_breakdown = compute_and_export()
        data = rebuild_dashboard_json(monthly, kpi_summary, category_breakdown, region_breakdown)
        rebuild_dashboard_html(json.dumps(data, default=str))
        elapsed = (datetime.now() - started).total_seconds()
        log.info(f"Done in {elapsed:.1f}s. Latest month: {kpi_summary.set_index('metric').loc['latest_month','value']}, "
                 f"revenue: {kpi_summary.set_index('metric').loc['latest_month_revenue','value']}")
    except Exception:
        log.exception("KPI regeneration FAILED")
        sys.exit(1)


if __name__ == "__main__":
    main()
