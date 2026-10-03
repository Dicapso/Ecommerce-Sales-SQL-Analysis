"""Generate a synthetic SQLite database for an Azerbaijani online retailer.

Tables: customers, products, orders_raw, order_items (orders 2024-01-01 .. 2025-12-31).
The data is synthetic (fixed seed, no real people) but has realistic patterns:
Baku-heavy demand, seasonal peaks (Novruz in March, Black Friday in November,
New Year in December), cash-on-delivery orders that get cancelled more often,
slower delivery to the regions driving cancellations, and repeat buyers.
"""
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 7
N_CUSTOMERS = 6000
START, END = pd.Timestamp("2024-01-01"), pd.Timestamp("2025-12-31")
DB_PATH = Path(__file__).parent / "ecommerce.db"

CITIES = {  # city: (share of customers, base delivery days)
    "Baku": (0.58, 1), "Sumgait": (0.10, 2), "Ganja": (0.09, 3), "Mingachevir": (0.05, 4),
    "Lankaran": (0.05, 5), "Shirvan": (0.04, 4), "Sheki": (0.05, 5), "Guba": (0.04, 4),
}
CATEGORIES = {  # category: (n products, price range AZN, margin range)
    "Electronics": (40, (40, 1500), (0.10, 0.20)),
    "Home & Kitchen": (45, (10, 400), (0.25, 0.40)),
    "Fashion": (60, (15, 250), (0.40, 0.60)),
    "Beauty": (40, (5, 120), (0.45, 0.65)),
    "Kids & Toys": (35, (8, 180), (0.30, 0.45)),
    "Groceries": (30, (2, 40), (0.10, 0.20)),
}
CAT_WEIGHT = {"Electronics": 0.09, "Home & Kitchen": 0.19, "Fashion": 0.25,
              "Beauty": 0.19, "Kids & Toys": 0.12, "Groceries": 0.16}
# multiplicative monthly seasonality (Jan..Dec)
SEASON = np.array([0.85, 0.85, 1.25, 0.95, 0.95, 0.90, 0.85, 0.90, 1.05, 1.00, 1.45, 1.55])


def generate(seed: int = SEED):
    rng = np.random.default_rng(seed)

    # --- products -----------------------------------------------------------
    rows, pid = [], 1
    for cat, (n, (lo, hi), (mlo, mhi)) in CATEGORIES.items():
        prices = np.exp(rng.uniform(np.log(lo), np.log(hi), n)).round(2)
        margins = rng.uniform(mlo, mhi, n)
        for i in range(n):
            rows.append((pid, f"{cat.split()[0]}-{i + 1:03d}", cat, prices[i],
                         round(prices[i] * (1 - margins[i]), 2)))
            pid += 1
    products = pd.DataFrame(rows, columns=["product_id", "product_name", "category", "list_price", "unit_cost"])
    # popularity: a few products sell much more (long tail)
    products["popularity"] = rng.pareto(1.6, len(products)) + 0.2

    # --- customers ----------------------------------------------------------
    city_names = list(CITIES)
    city_p = np.array([CITIES[c][0] for c in city_names])
    days = (END - START).days
    # ~40% of the base signed up in 2023 (existing customers), the rest join over 2024-2025
    first_signup = pd.Timestamp("2023-01-01")
    span = (END - first_signup).days
    signup = first_signup + pd.to_timedelta((rng.beta(1.0, 1.3, N_CUSTOMERS) * span).astype(int), unit="D")
    customers = pd.DataFrame({
        "customer_id": np.arange(1, N_CUSTOMERS + 1),
        "city": rng.choice(city_names, N_CUSTOMERS, p=city_p / city_p.sum()),
        "signup_date": signup.date,
        "acquisition_channel": rng.choice(["Instagram", "Google Ads", "Organic", "Referral", "TikTok"],
                                          N_CUSTOMERS, p=[0.32, 0.20, 0.25, 0.10, 0.13]),
        "gender": rng.choice(["F", "M"], N_CUSTOMERS, p=[0.56, 0.44]),
    })
    # customer purchase intensity (heavy-tailed -> loyal core)
    intensity = rng.gamma(0.7, 1.0, N_CUSTOMERS) * np.where(customers["acquisition_channel"] == "Referral", 1.5, 1.0)

    # --- orders -------------------------------------------------------------
    orders, items = [], []
    oid = 1
    for c in customers.itertuples(index=False):
        active_from = max(pd.Timestamp(c.signup_date), START)  # orders are only available from 2024
        active_days = (END - active_from).days + 1
        lam = intensity[c.customer_id - 1] * active_days / 150
        n_orders = rng.poisson(lam) + (1 if rng.random() < 0.85 else 0)
        if n_orders == 0:
            continue
        # sample order dates weighted by seasonality and slow growth over time
        cand = active_from + pd.to_timedelta(np.arange(active_days), unit="D")
        w = SEASON[cand.month - 1] * (1 + 0.25 * (cand - START).days / days)
        w = np.where((cand.month == 11) & (cand.day >= 24), w * 3, w)  # Black Friday week
        w = np.where((cand.month == 3) & (cand.day.isin(range(15, 22))), w * 2, w)  # Novruz week
        dates = np.sort(rng.choice(cand, n_orders, p=w / w.sum()))
        base_delivery = CITIES[c.city][1]
        for d in dates:
            payment = rng.choice(["Card", "Cash on delivery", "BNPL"], p=[0.55, 0.35, 0.10])
            delivery = max(1, base_delivery + rng.poisson(0.8) + (2 if pd.Timestamp(d).month in (11, 12) else 0))
            p_cancel = 0.04 + 0.08 * (payment == "Cash on delivery") + 0.018 * (delivery - 1)
            p_return = 0.05
            r = rng.random()
            status = "Cancelled" if r < p_cancel else ("Returned" if r < p_cancel + p_return else "Delivered")
            n_items = 1 + rng.poisson(0.7)
            cats = rng.choice(list(CAT_WEIGHT), n_items, p=list(CAT_WEIGHT.values()))
            disc_event = pd.Timestamp(d).month == 11 and pd.Timestamp(d).day >= 24
            for cat in cats:
                pool = products[products["category"] == cat]
                prod = pool.sample(1, weights=pool["popularity"], random_state=int(rng.integers(1e9))).iloc[0]
                qty = 1 + (rng.poisson(1.5) if cat == "Groceries" else rng.poisson(0.2))
                disc = rng.choice([0, 0.05, 0.10, 0.15, 0.20, 0.30], p=[0.55, 0.15, 0.12, 0.08, 0.06, 0.04])
                if disc_event:
                    disc = max(disc, rng.choice([0.20, 0.30, 0.40]))
                items.append((oid, int(prod.product_id), int(qty), float(prod.list_price), float(disc)))
            orders.append((oid, c.customer_id, str(pd.Timestamp(d).date()), status, payment, int(delivery)))
            oid += 1

    orders = pd.DataFrame(orders, columns=["order_id", "customer_id", "order_date", "status",
                                           "payment_method", "delivery_days"])
    items = pd.DataFrame(items, columns=["order_id", "product_id", "quantity", "unit_price", "discount"])
    items.insert(0, "item_id", np.arange(1, len(items) + 1))

    # inject a few data-quality issues to clean in SQL
    dup = orders.sample(25, random_state=seed).copy()  # exact duplicate rows
    orders = pd.concat([orders, dup]).reset_index(drop=True)
    orders.loc[orders.sample(40, random_state=seed + 1).index, "delivery_days"] = None
    return customers, products.drop(columns="popularity"), orders, items


def main():
    customers, products, orders, items = generate()
    DB_PATH.unlink(missing_ok=True)
    with sqlite3.connect(DB_PATH) as con:
        customers.to_sql("customers", con, index=False)
        products.to_sql("products", con, index=False)
        orders.to_sql("orders_raw", con, index=False)
        items.to_sql("order_items", con, index=False)
    print(f"customers={len(customers):,} products={len(products):,} "
          f"orders_raw={len(orders):,} order_items={len(items):,} -> {DB_PATH.name}")


if __name__ == "__main__":
    main()
