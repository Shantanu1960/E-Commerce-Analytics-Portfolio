"""
clean_and_load.py
------------------
Cleans the raw Olist-style CSVs and loads them into PostgreSQL as a proper
relational schema. This is the logic that also lives (with more narrative)
inside notebooks/01_etl_pipeline.ipynb -- kept here as a standalone, re-runnable
script so the pipeline can be scheduled / re-run outside a notebook.
"""

import re
import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

DB_URL = "postgresql+psycopg2://postgres:postgres@localhost:5432/ecommerce"

# ---------------------------------------------------------------------------
# 1. LOAD RAW
# ---------------------------------------------------------------------------
customers = pd.read_csv("raw/customers_raw.csv")
sellers = pd.read_csv("raw/sellers_raw.csv")
products = pd.read_csv("raw/products_raw.csv")
orders = pd.read_csv("raw/orders_raw.csv")
order_items = pd.read_csv("raw/order_items_raw.csv")

print("RAW ROW COUNTS:", {n: len(d) for n, d in
      [("customers", customers), ("sellers", sellers), ("products", products),
       ("orders", orders), ("order_items", order_items)]})

# ---------------------------------------------------------------------------
# 2. CLEAN: CUSTOMERS
# ---------------------------------------------------------------------------
customers = customers.drop_duplicates()
customers["customer_city"] = (
    customers["customer_city"].str.strip().str.title()
)
customers["customer_state"] = customers["customer_state"].str.strip().str.upper()
customers["customer_zip_code_prefix"] = customers["customer_zip_code_prefix"].astype("Int64")

# ---------------------------------------------------------------------------
# 3. CLEAN: SELLERS
# ---------------------------------------------------------------------------
sellers = sellers.drop_duplicates()
sellers["seller_city"] = sellers["seller_city"].str.strip().str.title()
sellers["seller_state"] = sellers["seller_state"].str.strip().str.upper()

# ---------------------------------------------------------------------------
# 4. CLEAN: PRODUCTS -- standardize category names to a canonical snake_case set
# ---------------------------------------------------------------------------
products = products.drop_duplicates(subset="product_id")

def normalize_category(val):
    if pd.isna(val):
        return "unknown"
    v = str(val).strip().lower()
    v = re.sub(r"\s+", "_", v)          # spaces -> underscore
    v = re.sub(r"[^a-z_]", "", v)       # strip stray punctuation
    return v

products["product_category_name"] = products["product_category_name"].apply(normalize_category)
# collapse near-duplicate spellings (e.g. "fashionclothing" -> "fashion_clothing")
CANON = ["electronics", "home_appliances", "furniture", "sports_leisure", "toys",
         "beauty_health", "fashion_clothing", "books", "auto_parts", "garden_tools",
         "pet_supplies", "computers_accessories", "watches_gifts", "baby", "stationery"]
nospace_lookup = {c.replace("_", ""): c for c in CANON}
products["product_category_name"] = products["product_category_name"].apply(
    lambda v: nospace_lookup.get(v.replace("_", ""), v)
)
products["product_weight_g"] = pd.to_numeric(products["product_weight_g"], errors="coerce")
products["product_weight_g"] = products["product_weight_g"].fillna(products["product_weight_g"].median())

# ---------------------------------------------------------------------------
# 5. CLEAN: ORDERS -- parse mixed date formats, dedupe, validate status
# ---------------------------------------------------------------------------
orders = orders.drop_duplicates()

DATE_COLS = ["order_purchase_timestamp", "order_approved_at",
             "order_delivered_carrier_date", "order_delivered_customer_date",
             "order_estimated_delivery_date"]

def parse_mixed_date(s):
    if pd.isna(s):
        return pd.NaT
    s = str(s).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M"):
        try:
            return pd.to_datetime(s, format=fmt)
        except ValueError:
            continue
    return pd.to_datetime(s, errors="coerce")  # last-resort fallback

for col in DATE_COLS:
    orders[col] = orders[col].apply(parse_mixed_date)

valid_status = {"delivered", "shipped", "processing", "canceled", "unavailable"}
orders = orders[orders["order_status"].isin(valid_status)]
# drop orders pointing at a customer_id we don't actually have
orders = orders[orders["customer_id"].isin(customers["customer_id"])]

# ---------------------------------------------------------------------------
# 6. CLEAN: ORDER_ITEMS -- parse currency strings, drop orphaned FKs, fix bad prices
# ---------------------------------------------------------------------------
order_items = order_items.drop_duplicates()

def parse_price(val):
    if pd.isna(val):
        return np.nan
    s = str(val).strip()
    s = s.replace("R$", "").strip()
    if "," in s and "." in s:           # "1.234,56" style
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:                       # "39,74" -> "39.74"
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return np.nan

order_items["price"] = order_items["price"].apply(parse_price)
order_items["freight_value"] = pd.to_numeric(order_items["freight_value"], errors="coerce")
order_items["freight_value"] = order_items["freight_value"].fillna(order_items["freight_value"].median())

# drop bad-data rows: non-positive price (entry errors) -- explicit, logged decision
bad_price_rows = (order_items["price"] <= 0) | order_items["price"].isna()
print(f"Dropping {bad_price_rows.sum()} order_items rows with invalid price")
order_items = order_items[~bad_price_rows]

# drop orphaned product_id / order_id references (referential integrity)
orphan_products = ~order_items["product_id"].isin(products["product_id"])
orphan_orders = ~order_items["order_id"].isin(orders["order_id"])
print(f"Dropping {orphan_products.sum()} order_items rows with unknown product_id")
print(f"Dropping {orphan_orders.sum()} order_items rows with unknown order_id (dup/bad order rows removed above)")
order_items = order_items[~orphan_products & ~orphan_orders]

# ---------------------------------------------------------------------------
# 7. LOAD INTO POSTGRES
# ---------------------------------------------------------------------------
engine = create_engine(DB_URL)

with engine.begin() as conn:
    conn.execute(text("DROP TABLE IF EXISTS order_items CASCADE"))
    conn.execute(text("DROP TABLE IF EXISTS orders CASCADE"))
    conn.execute(text("DROP TABLE IF EXISTS products CASCADE"))
    conn.execute(text("DROP TABLE IF EXISTS sellers CASCADE"))
    conn.execute(text("DROP TABLE IF EXISTS customers CASCADE"))

customers.to_sql("customers", engine, if_exists="replace", index=False)
sellers.to_sql("sellers", engine, if_exists="replace", index=False)
products.to_sql("products", engine, if_exists="replace", index=False)
orders.to_sql("orders", engine, if_exists="replace", index=False)
order_items.to_sql("order_items", engine, if_exists="replace", index=False)

# add keys / constraints / indexes after load (faster than enforcing during insert)
with engine.begin() as conn:
    conn.execute(text("ALTER TABLE customers ADD PRIMARY KEY (customer_id)"))
    conn.execute(text("ALTER TABLE sellers ADD PRIMARY KEY (seller_id)"))
    conn.execute(text("ALTER TABLE products ADD PRIMARY KEY (product_id)"))
    conn.execute(text("ALTER TABLE orders ADD PRIMARY KEY (order_id)"))
    conn.execute(text("ALTER TABLE orders ADD CONSTRAINT fk_orders_customer "
                       "FOREIGN KEY (customer_id) REFERENCES customers(customer_id)"))
    conn.execute(text("ALTER TABLE order_items ADD COLUMN id SERIAL PRIMARY KEY"))
    conn.execute(text("ALTER TABLE order_items ADD CONSTRAINT fk_oi_order "
                       "FOREIGN KEY (order_id) REFERENCES orders(order_id)"))
    conn.execute(text("ALTER TABLE order_items ADD CONSTRAINT fk_oi_product "
                       "FOREIGN KEY (product_id) REFERENCES products(product_id)"))
    conn.execute(text("ALTER TABLE order_items ADD CONSTRAINT fk_oi_seller "
                       "FOREIGN KEY (seller_id) REFERENCES sellers(seller_id)"))
    conn.execute(text("CREATE INDEX idx_orders_customer ON orders(customer_id)"))
    conn.execute(text("CREATE INDEX idx_orders_purchase_ts ON orders(order_purchase_timestamp)"))
    conn.execute(text("CREATE INDEX idx_oi_order ON order_items(order_id)"))
    conn.execute(text("CREATE INDEX idx_oi_product ON order_items(product_id)"))
    conn.execute(text("CREATE INDEX idx_oi_seller ON order_items(seller_id)"))

print("\nCLEAN ROW COUNTS LOADED TO POSTGRES:")
print({n: len(d) for n, d in
      [("customers", customers), ("sellers", sellers), ("products", products),
       ("orders", orders), ("order_items", order_items)]})
print("\nDone. Database: ecommerce")
