"""
generate_raw_data.py
---------------------
Generates a synthetic, intentionally messy e-commerce dataset modeled on the
real Olist Brazilian E-Commerce schema (https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce).

Why synthetic: the sandbox this was built in cannot reach kaggle.com or
fakestoreapi.com (network allowlist only includes package registries + GitHub).
The schema, column names, and messiness patterns below mirror the real Olist
dataset closely enough that the cleaning/modeling/SQL work transfers directly
to the real CSVs if you swap them in.

Messiness injected on purpose (so the cleaning step in the notebook has real
work to do):
  - customer_id is unique PER ORDER, customer_unique_id is the real person
    (this is how Olist actually works -- a classic gotcha for retention calcs)
  - mixed date formats (ISO vs DD/MM/YYYY) in order timestamps
  - currency stored inconsistently ("R$ 129,90" vs "129.90" vs "129.9")
  - inconsistent category casing/typos/whitespace ("eletronicos", "Eletrônicos ", "ELETRONICOS")
  - missing freight values, missing delivery dates for undelivered orders
  - ~1% exact duplicate rows in orders and order_items
  - ~0.5% order_items referencing a product_id that doesn't exist in products (orphaned FK)
  - a handful of negative/zero price rows (data entry errors)
"""

import random
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from faker import Faker

random.seed(42)
np.random.seed(42)
fake = Faker("pt_BR")
Faker.seed(42)

OUT = "raw"
import os
os.makedirs(OUT, exist_ok=True)

N_CUSTOMERS = 8000          # unique people (customer_unique_id)
N_SELLERS = 300
N_PRODUCTS = 1200
N_ORDERS = 20000

STATES = ["SP", "RJ", "MG", "RS", "PR", "SC", "BA", "DF", "GO", "PE", "CE", "PA", "ES", "AM"]
STATE_WEIGHTS = [0.30, 0.13, 0.12, 0.07, 0.07, 0.05, 0.06, 0.04, 0.04, 0.03, 0.03, 0.02, 0.02, 0.02]

CATEGORY_CANON = [
    "electronics", "home_appliances", "furniture", "sports_leisure", "toys",
    "beauty_health", "fashion_clothing", "books", "auto_parts", "garden_tools",
    "pet_supplies", "computers_accessories", "watches_gifts", "baby", "stationery",
]

# Map each canonical category to a few "dirty" variants seen in raw exports
def dirty_variants(cat):
    base = cat.replace("_", " ")
    return [
        cat,                                   # clean snake_case
        base.title() + " ",                    # title case + trailing space
        base.upper(),                          # all caps
        base.replace(" ", ""),                 # no separator
        " " + base.lower(),                    # leading space
    ]

CATEGORY_DIRTY_MAP = {cat: dirty_variants(cat) for cat in CATEGORY_CANON}

# ---------------------------------------------------------------------------
# CUSTOMERS  (customer_unique_id = real person; customer_id = per-order key)
# ---------------------------------------------------------------------------
unique_people = []
for i in range(N_CUSTOMERS):
    state = np.random.choice(STATES, p=STATE_WEIGHTS)
    unique_people.append({
        "customer_unique_id": f"cu_{i:06d}",
        "customer_city": fake.city(),
        "customer_state": state,
        "customer_zip_code_prefix": fake.postcode().replace("-", "")[:5],
    })

customers_rows = []
cid_counter = 0
# ~18% of people place more than one order -> multiple customer_id rows per unique_id
for person in unique_people:
    n_order_identities = np.random.choice([1, 2, 3], p=[0.82, 0.14, 0.04])
    for _ in range(n_order_identities):
        city = person["customer_city"]
        # mess up casing/whitespace on city ~30% of the time
        r = random.random()
        if r < 0.10:
            city = city.upper()
        elif r < 0.20:
            city = city.lower() + "  "
        elif r < 0.25:
            city = " " + city
        customers_rows.append({
            "customer_id": f"c_{cid_counter:07d}",
            "customer_unique_id": person["customer_unique_id"],
            "customer_city": city,
            "customer_state": person["customer_state"],
            "customer_zip_code_prefix": person["customer_zip_code_prefix"] if random.random() > 0.02 else None,
        })
        cid_counter += 1

customers_df = pd.DataFrame(customers_rows)
all_customer_ids = customers_df["customer_id"].tolist()

# ---------------------------------------------------------------------------
# SELLERS
# ---------------------------------------------------------------------------
sellers_rows = []
for i in range(N_SELLERS):
    sellers_rows.append({
        "seller_id": f"s_{i:05d}",
        "seller_city": fake.city(),
        "seller_state": np.random.choice(STATES, p=STATE_WEIGHTS),
    })
sellers_df = pd.DataFrame(sellers_rows)

# ---------------------------------------------------------------------------
# PRODUCTS
# ---------------------------------------------------------------------------
products_rows = []
for i in range(N_PRODUCTS):
    cat = np.random.choice(CATEGORY_CANON)
    variant = np.random.choice(CATEGORY_DIRTY_MAP[cat])
    # 2% of products have a missing/null category (common in real exports)
    if random.random() < 0.02:
        variant = None
    products_rows.append({
        "product_id": f"p_{i:05d}",
        "product_category_name": variant,
        "product_weight_g": int(np.random.lognormal(mean=6.5, sigma=1.0)),
    })
products_df = pd.DataFrame(products_rows)
all_product_ids = products_df["product_id"].tolist()

# base price per product (lognormal -> realistic right-skewed price distribution)
product_base_price = {pid: round(float(np.random.lognormal(mean=4.0, sigma=0.9)), 2) for pid in all_product_ids}

# ---------------------------------------------------------------------------
# ORDERS
# ---------------------------------------------------------------------------
START = datetime(2023, 1, 1)
END = datetime(2024, 12, 31)
span_days = (END - START).days

STATUS_CHOICES = ["delivered", "shipped", "processing", "canceled", "unavailable"]
STATUS_WEIGHTS = [0.84, 0.06, 0.04, 0.04, 0.02]

def fmt_date_messy(dt, dirty_prob=0.12):
    """Return a date as ISO string normally, but DD/MM/YYYY HH:MM sometimes."""
    if dt is None:
        return None
    if random.random() < dirty_prob:
        return dt.strftime("%d/%m/%Y %H:%M")
    return dt.strftime("%Y-%m-%d %H:%M:%S")

orders_rows = []
order_ids = []
for i in range(N_ORDERS):
    order_id = f"o_{i:06d}"
    order_ids.append(order_id)
    customer_id = random.choice(all_customer_ids)
    status = np.random.choice(STATUS_CHOICES, p=STATUS_WEIGHTS)

    purchase_dt = START + timedelta(days=random.randint(0, span_days),
                                     hours=random.randint(0, 23), minutes=random.randint(0, 59))
    approved_dt = purchase_dt + timedelta(hours=random.randint(1, 48))
    est_delivery_dt = purchase_dt + timedelta(days=random.randint(7, 25))

    carrier_dt = None
    delivered_dt = None
    if status in ("delivered", "shipped"):
        carrier_dt = approved_dt + timedelta(days=random.randint(0, 3))
    if status == "delivered":
        # ~13% of delivered orders arrive late vs. estimate
        if random.random() < 0.13:
            delivered_dt = est_delivery_dt + timedelta(days=random.randint(1, 10))
        else:
            delivered_dt = est_delivery_dt - timedelta(days=random.randint(0, 6))
        if delivered_dt < carrier_dt:
            delivered_dt = carrier_dt + timedelta(days=random.randint(1, 4))

    orders_rows.append({
        "order_id": order_id,
        "customer_id": customer_id,
        "order_status": status,
        "order_purchase_timestamp": fmt_date_messy(purchase_dt),
        "order_approved_at": fmt_date_messy(approved_dt) if random.random() > 0.01 else None,
        "order_delivered_carrier_date": fmt_date_messy(carrier_dt),
        "order_delivered_customer_date": fmt_date_messy(delivered_dt),
        "order_estimated_delivery_date": fmt_date_messy(est_delivery_dt, dirty_prob=0.0),
    })

orders_df = pd.DataFrame(orders_rows)
# inject ~1% exact duplicate rows
dupe_sample = orders_df.sample(frac=0.01, random_state=1)
orders_df = pd.concat([orders_df, dupe_sample], ignore_index=True)

# ---------------------------------------------------------------------------
# ORDER ITEMS  (1-3 items per order)
# ---------------------------------------------------------------------------
def messy_price(value):
    """Return price formatted as plain float string, comma-decimal BRL, or BRL-prefixed."""
    r = random.random()
    if r < 0.15:
        return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    elif r < 0.25:
        return f"{value:.2f}".replace(".", ",")
    else:
        return f"{value:.2f}"

order_items_rows = []
item_counter = 0
for order_id in order_ids:
    n_items = np.random.choice([1, 2, 3], p=[0.7, 0.22, 0.08])
    chosen_products = random.sample(all_product_ids, k=min(n_items, len(all_product_ids)))
    for item_no, product_id in enumerate(chosen_products, start=1):
        # ~0.5% orphaned product reference (FK mismatch, real-world messiness)
        pid = product_id
        if random.random() < 0.005:
            pid = f"p_{99000 + item_counter % 500:05d}"  # references a product_id that doesn't exist

        base_price = product_base_price.get(product_id, 50.0)
        noise = np.random.normal(0, base_price * 0.05)
        price_val = max(base_price + noise, 1.0)
        # rare data entry errors: zero/negative price
        if random.random() < 0.002:
            price_val = round(random.uniform(-20, 0), 2)

        freight = round(max(np.random.normal(15, 6), 0), 2)
        freight_str = freight
        if random.random() < 0.03:
            freight_str = None  # missing freight value

        seller_id = random.choice(sellers_df["seller_id"].tolist())

        order_items_rows.append({
            "order_id": order_id,
            "order_item_id": item_no,
            "product_id": pid,
            "seller_id": seller_id,
            "price": messy_price(price_val),
            "freight_value": freight_str,
        })
        item_counter += 1

order_items_df = pd.DataFrame(order_items_rows)
dupe_sample_oi = order_items_df.sample(frac=0.01, random_state=2)
order_items_df = pd.concat([order_items_df, dupe_sample_oi], ignore_index=True)

# ---------------------------------------------------------------------------
# WRITE RAW CSVs (this simulates the "pull from source" step)
# ---------------------------------------------------------------------------
customers_df.to_csv(f"{OUT}/customers_raw.csv", index=False)
sellers_df.to_csv(f"{OUT}/sellers_raw.csv", index=False)
products_df.to_csv(f"{OUT}/products_raw.csv", index=False)
orders_df.to_csv(f"{OUT}/orders_raw.csv", index=False)
order_items_df.to_csv(f"{OUT}/order_items_raw.csv", index=False)

print("Raw files written to ./raw/:")
for f in ["customers_raw.csv", "sellers_raw.csv", "products_raw.csv", "orders_raw.csv", "order_items_raw.csv"]:
    df = pd.read_csv(f"{OUT}/{f}")
    print(f"  {f:28s} rows={len(df):>7,}  cols={list(df.columns)}")
