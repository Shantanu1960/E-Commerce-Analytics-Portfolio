# Project 1 — API/CSV → Pandas → PostgreSQL → SQL Insights

Full pipeline proof-of-concept: pull raw data, clean it with pandas, model it relationally, load it into
Postgres, answer business questions in SQL.



- Mixed date formats (`YYYY-MM-DD HH:MM:SS` vs `DD/MM/YYYY HH:MM`)
- Currency stored inconsistently (`"129.90"`, `"129,90"`, `"R$ 129,90"`)
- Category names with inconsistent casing/spacing/typos (`"electronics"`, `"ELECTRONICS"`, `" Electronics "`)
- Exact duplicate rows in `orders` and `order_items`
- Orphaned foreign keys (`order_items.product_id` referencing a product that doesn't exist)
- Missing values (freight value, delivery dates for undelivered orders, zip codes)
- A `customer_id` (per-order identity) vs. `customer_unique_id` (real person) split — a genuine Olist quirk
  that breaks naive retention calculations if you group by the wrong column

**Everything downstream — cleaning logic, schema, SQL — is written generically enough to run unchanged against
the real Olist CSVs.** Swap `generate_raw_data.py`'s output for the real files and the rest of the pipeline
doesn't change.

To regenerate the raw data: `python3 generate_raw_data.py` (seeded, so it's reproducible).

## Pipeline

```
generate_raw_data.py   →  raw/*.csv               (simulates "pull from source")
clean_and_load.py      →  PostgreSQL `ecommerce`   (clean + model + load, standalone/schedulable)
notebooks/01_etl_pipeline.ipynb                    (same pipeline, narrated, with EDA + all 8 SQL questions executed)
queries.sql                                        (the 8 business questions as plain SQL)
```

Run order:
```bash
pip install pandas numpy sqlalchemy psycopg2-binary faker
python3 generate_raw_data.py
python3 clean_and_load.py        # requires a running Postgres + a database named `ecommerce`
psql -d ecommerce -f queries.sql # or open the notebook
```

## Schema

5 tables — `order_items` is the fact table (item-level grain); `customers`, `sellers`, `products` are
dimensions; `orders` is the order-level grain (status + timestamps), separate from item-level price/seller.

```
customers (customer_id PK, customer_unique_id, customer_city, customer_state, customer_zip_code_prefix)
sellers   (seller_id PK, seller_city, seller_state)
products  (product_id PK, product_category_name, product_weight_g)
orders    (order_id PK, customer_id FK → customers, order_status, order_purchase_timestamp,
           order_approved_at, order_delivered_carrier_date, order_delivered_customer_date,
           order_estimated_delivery_date)
order_items (id PK, order_id FK → orders, order_item_id, product_id FK → products,
             seller_id FK → sellers, price, freight_value)
```

Loaded with real primary keys, foreign keys, and indexes on the join/filter columns (see `clean_and_load.py`).

## Cleaning decisions (what got dropped/changed, and why)

| Issue | Decision |
|---|---|
| Duplicate rows in `orders`/`order_items` | Dropped exact duplicates |
| Mixed date formats | Parsed explicitly trying both known formats rather than trusting a single `pd.to_datetime` pass (which silently mis-parses ambiguous dates) |
| Currency strings (`R$`, comma-decimals) | Parsed into floats with explicit logic for each format |
| Category name variants | Normalized to snake_case, collapsed near-duplicates to a canonical list, nulls → `"unknown"` |
| Non-positive/unparsable prices | Dropped (~70 rows) — logged as a data-entry-error class, not silently zeroed |
| Orphaned `product_id` in `order_items` | Dropped (~133 rows) — referential integrity enforced before load |
| Missing freight value | Imputed with median (small % missing, numeric, low-stakes field) |
| Missing zip code | Left as null (not used in any of the 8 queries; imputing would be guessing) |

Full row-count-before/after audit trail is in the notebook output.

## Business questions: question → query → finding

| # | Question | Finding |
|---|---|---|
| 1 | Monthly revenue trend | Revenue is roughly flat (~$75-85K/month) across the two years with no clear seasonal trend — growth needs a deliberate driver, it isn't compounding on its own. |
| 2 | Month-over-month revenue growth % | Swings between about -17% and +15% with no consistent direction — noise around a flat baseline; a single month shouldn't be over-read in a leadership update. |
| 3 | Top 10 products by revenue | Dominated by a handful of high-unit-price `pet_supplies`/`auto_parts` SKUs rather than high volume — these warrant individual reorder-point monitoring; a stockout here does outsized damage. |
| 4 | Top categories by revenue & order count | `pet_supplies` and `auto_parts` lead on revenue via higher average item price, not more orders — a "grow the basket" play likely beats a "grow traffic" play in these categories. |
| 5 | Customer retention (% ordering more than once) | A clear majority of customers are repeat buyers. The methodology matters more than the exact number: grouping by `customer_id` instead of `customer_unique_id` silently produces a much lower, wrong figure. |
| 6 | Average order value by state | AOV ranges roughly $111-$129 across states — geography is a secondary lever versus category mix; not a large enough gap to justify regional pricing complexity on its own. |
| 7 | Late delivery rate by seller | Platform-wide late rate is ~13%, but the worst sellers run 20-25% — a short, actionable list for a seller-scorecard conversation rather than a platform-wide fix. |
| 8 | Avg delivery time (actual vs. promised) by state | Actual delivery beats the promised estimate by ~1.5-2 days everywhere — good operationally, but suggests the estimated-delivery model is padded more than necessary; tightening it is a low-risk checkout-experience win. |

Full SQL for each question is in `queries.sql`, and each runs (with output) inside the notebook.

## Files

```
generate_raw_data.py            # synthetic raw data generator (seeded/reproducible)
clean_and_load.py               # standalone cleaning + Postgres load script
queries.sql                     # the 8 business questions
notebooks/01_etl_pipeline.ipynb # full narrated pipeline + EDA + all 8 queries executed with output
raw/                            # generated raw CSVs (gitignored by default — regenerate, don't commit)
```
