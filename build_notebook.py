"""Build sales_analysis.ipynb with nbformat (run, then execute with nbconvert)."""
import nbformat as nbf

nb = nbf.v4.new_notebook()
cells = []
md = lambda s: cells.append(nbf.v4.new_markdown_cell(s.strip()))
code = lambda s: cells.append(nbf.v4.new_code_cell(s.strip()))

md("""
# 🛒 E-commerce Sales Analysis with SQL — Azerbaijani Online Store
**Daily Data Analytics Project · Day 02**

## 1. Business question
An online retailer selling across 8 Azerbaijani cities (2024–2025) wants to know:
1. How are revenue, orders and average order value (AOV) trending, and how strong is seasonality?
2. Which categories make the **profit** — not just the revenue?
3. Why are some orders cancelled, and how much revenue does that cost?
4. How dependent is the business on its best customers, and do customers come back?

All analysis is done in **SQL (SQLite)** — CTEs, window functions (`LAG`, `RANK`, `NTILE`, running sums), `CASE` logic — with pandas/matplotlib only for display.
""")

code("""
import sqlite3
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
from pathlib import Path

pd.set_option("display.float_format", lambda v: f"{v:,.2f}")
plt.rcParams.update({"figure.dpi": 110, "axes.spines.top": False, "axes.spines.right": False,
                     "axes.grid": True, "grid.alpha": 0.3})
BLUE, ORANGE, GREEN, RED, GREY = "#2E6FBA", "#E8833A", "#3A9D5D", "#C8453B", "#8A8F98"
IMG = Path("images"); IMG.mkdir(exist_ok=True)

con = sqlite3.connect("data/ecommerce.db")
def q(sql):
    return pd.read_sql_query(sql, con)

q("SELECT name, type FROM sqlite_master ORDER BY type, name")
""")

md("## 2. Data quality check")
code("""
q('''
SELECT 'customers' AS tbl, COUNT(*) AS n_rows FROM customers
UNION ALL SELECT 'products', COUNT(*) FROM products
UNION ALL SELECT 'orders_raw', COUNT(*) FROM orders_raw
UNION ALL SELECT 'order_items', COUNT(*) FROM order_items
''')
""")
code("""
q('''
SELECT
  (SELECT COUNT(*) FROM orders_raw) - (SELECT COUNT(DISTINCT order_id) FROM orders_raw) AS duplicate_order_rows,
  (SELECT COUNT(*) FROM orders_raw WHERE delivery_days IS NULL)                         AS missing_delivery_days,
  (SELECT COUNT(*) FROM order_items i LEFT JOIN products p USING(product_id)
     WHERE p.product_id IS NULL)                                                       AS orphan_items,
  (SELECT COUNT(*) FROM orders_raw o LEFT JOIN customers c USING(customer_id)
     WHERE c.customer_id IS NULL)                                                      AS orphan_orders,
  (SELECT MIN(order_date) FROM orders_raw) AS first_order,
  (SELECT MAX(order_date) FROM orders_raw) AS last_order
''')
""")
md("""
**Cleaning:** 25 orders were loaded twice (exact duplicates) and 40 have no `delivery_days`. A clean `orders` view keeps one row per order and imputes missing delivery time with the city median. A `sales` view joins everything at line level and calculates net revenue (after discount) and gross profit.
""")
code("""
con.executescript('''
DROP VIEW IF EXISTS orders;
CREATE VIEW orders AS
WITH dedup AS (SELECT DISTINCT * FROM orders_raw),
city_median AS (
  SELECT c.city, AVG(d.delivery_days) AS med
  FROM (SELECT o.*, c.city,
               ROW_NUMBER() OVER (PARTITION BY c.city ORDER BY o.delivery_days) AS rn,
               COUNT(*)     OVER (PARTITION BY c.city) AS cnt
        FROM dedup o JOIN customers c USING(customer_id)
        WHERE o.delivery_days IS NOT NULL) d
  JOIN customers c ON c.customer_id = d.customer_id
  WHERE d.rn IN ((d.cnt + 1) / 2, (d.cnt + 2) / 2)
  GROUP BY c.city
)
SELECT o.order_id, o.customer_id, o.order_date, o.status, o.payment_method,
       COALESCE(o.delivery_days, ROUND(m.med)) AS delivery_days
FROM dedup o
JOIN customers c USING(customer_id)
JOIN city_median m ON m.city = c.city;

DROP VIEW IF EXISTS sales;
CREATE VIEW sales AS
SELECT o.order_id, o.customer_id, o.order_date, strftime('%Y-%m', o.order_date) AS month,
       o.status, o.payment_method, o.delivery_days, c.city, c.acquisition_channel,
       p.category, p.product_name, i.quantity, i.discount,
       ROUND(i.quantity * i.unit_price * (1 - i.discount), 2)                    AS net_revenue,
       ROUND(i.quantity * (i.unit_price * (1 - i.discount) - p.unit_cost), 2)    AS gross_profit
FROM orders o
JOIN customers c   USING(customer_id)
JOIN order_items i USING(order_id)
JOIN products p    USING(product_id);
''')
q("SELECT COUNT(*) AS clean_orders, SUM(delivery_days IS NULL) AS still_missing FROM orders")
""")

md("## 3. Headline KPIs (delivered orders only)")
code("""
kpi = q('''
SELECT COUNT(DISTINCT order_id)                         AS orders,
       COUNT(DISTINCT customer_id)                      AS customers,
       ROUND(SUM(net_revenue))                          AS revenue_azn,
       ROUND(SUM(gross_profit))                         AS gross_profit_azn,
       ROUND(100.0 * SUM(gross_profit) / SUM(net_revenue), 1) AS gross_margin_pct,
       ROUND(SUM(net_revenue) / COUNT(DISTINCT order_id), 1)  AS aov_azn
FROM sales WHERE status = 'Delivered'
''')
status = q('''
SELECT status, COUNT(*) AS orders, ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct
FROM orders GROUP BY status ORDER BY orders DESC
''')
display(kpi.T.rename(columns={0: "value"}), status)
""")

md("## 4. Monthly trend & seasonality — `LAG` and running totals")
code("""
monthly = q('''
WITH m AS (
  SELECT month, SUM(net_revenue) AS revenue, COUNT(DISTINCT order_id) AS orders
  FROM sales WHERE status = 'Delivered' GROUP BY month
)
SELECT month, ROUND(revenue) AS revenue, orders, ROUND(revenue / orders, 1) AS aov,
       ROUND(100.0 * (revenue - LAG(revenue) OVER (ORDER BY month)) / LAG(revenue) OVER (ORDER BY month), 1) AS mom_pct,
       ROUND(100.0 * (revenue - LAG(revenue, 12) OVER (ORDER BY month)) / LAG(revenue, 12) OVER (ORDER BY month), 1) AS yoy_pct,
       ROUND(SUM(revenue) OVER (PARTITION BY substr(month, 1, 4) ORDER BY month)) AS ytd_revenue
FROM m ORDER BY month
''')
monthly
""")
code("""
yearly = q('''
SELECT substr(month,1,4) AS year, ROUND(SUM(net_revenue)) AS revenue,
       COUNT(DISTINCT order_id) AS orders, ROUND(SUM(net_revenue)/COUNT(DISTINCT order_id),1) AS aov
FROM sales WHERE status='Delivered' GROUP BY year
''')
yearly["growth_pct"] = (yearly["revenue"].pct_change() * 100).round(1)
yearly
""")
code("""
fig, ax = plt.subplots(figsize=(11, 4.5))
x = pd.to_datetime(monthly["month"])
ax.bar(x, monthly["revenue"] / 1000, width=20, color=BLUE, alpha=0.85, label="Revenue (K AZN)")
ax.set_ylabel("Revenue, K AZN")
ax2 = ax.twinx(); ax2.grid(False)
ax2.plot(x, monthly["aov"], color=ORANGE, marker="o", lw=2, label="AOV (AZN)")
ax2.set_ylabel("AOV, AZN"); ax2.spines["right"].set_visible(True)
for m, lab in [("2024-03", "Novruz"), ("2024-11", "Black Friday"), ("2025-03", "Novruz"), ("2025-11", "Black Friday")]:
    r = monthly[monthly["month"] == m].iloc[0]
    ax.annotate(lab, (pd.to_datetime(m), r["revenue"] / 1000), xytext=(0, 6), textcoords="offset points",
                ha="center", fontsize=8, color=GREY)
ax.set_title("Monthly delivered revenue and average order value, 2024–2025", loc="left", fontweight="bold")
fig.legend(loc="upper left", bbox_to_anchor=(0.07, 0.88), frameon=False)
fig.tight_layout(); fig.savefig(IMG / "monthly_trend.png", bbox_inches="tight"); plt.show()
""")

md("## 5. Category profitability — revenue vs. margin")
code("""
cat = q('''
SELECT category,
       ROUND(SUM(net_revenue)) AS revenue,
       ROUND(SUM(gross_profit)) AS gross_profit,
       ROUND(100.0 * SUM(gross_profit) / SUM(net_revenue), 1) AS margin_pct,
       ROUND(100.0 * SUM(net_revenue)  / SUM(SUM(net_revenue))  OVER (), 1) AS revenue_share_pct,
       ROUND(100.0 * SUM(gross_profit) / SUM(SUM(gross_profit)) OVER (), 1) AS profit_share_pct,
       ROUND(AVG(discount) * 100, 1) AS avg_discount_pct
FROM sales WHERE status = 'Delivered'
GROUP BY category ORDER BY gross_profit DESC
''')
cat
""")
code("""
top_products = q('''
WITH p AS (
  SELECT category, product_name, SUM(net_revenue) AS revenue, SUM(gross_profit) AS profit
  FROM sales WHERE status = 'Delivered' GROUP BY category, product_name
)
SELECT * FROM (
  SELECT category, product_name, ROUND(revenue) AS revenue, ROUND(profit) AS profit,
         RANK() OVER (PARTITION BY category ORDER BY profit DESC) AS rank_in_category
  FROM p)
WHERE rank_in_category <= 2 ORDER BY profit DESC
''')
top_products
""")
code("""
fig, axes = plt.subplots(1, 2, figsize=(12, 4.3))
c = cat.sort_values("revenue")
axes[0].barh(c["category"], c["revenue_share_pct"], color=GREY, label="Share of revenue")
axes[0].barh(c["category"], c["profit_share_pct"], color=BLUE, height=0.45, label="Share of gross profit")
axes[0].xaxis.set_major_formatter(mtick.PercentFormatter(decimals=0)); axes[0].legend(frameon=False, loc="lower right")
axes[0].set_title("Revenue share vs profit share", loc="left", fontweight="bold")
disc = q('''
SELECT CAST(discount*100 AS INT) AS discount_pct,
       ROUND(100.0 * SUM(gross_profit) / SUM(net_revenue), 1) AS margin_pct,
       COUNT(*) AS lines
FROM sales WHERE status='Delivered' GROUP BY discount_pct ORDER BY discount_pct
''')
axes[1].plot(disc["discount_pct"], disc["margin_pct"], marker="o", color=RED, lw=2)
axes[1].axhline(0, color="black", lw=0.8)
axes[1].xaxis.set_major_formatter(mtick.PercentFormatter(decimals=0)); axes[1].yaxis.set_major_formatter(mtick.PercentFormatter(decimals=0))
axes[1].set_xlabel("Discount on line"); axes[1].set_ylabel("Gross margin")
axes[1].set_title("Gross margin collapses as discounts deepen", loc="left", fontweight="bold")
fig.tight_layout(); fig.savefig(IMG / "category_profit_discount.png", bbox_inches="tight"); plt.show()
disc
""")

md("## 6. Cancellations — payment method, delivery time and city")
code("""
cancel_pay = q('''
SELECT payment_method, COUNT(*) AS orders,
       ROUND(100.0 * AVG(status = 'Cancelled'), 1) AS cancel_rate_pct,
       ROUND(100.0 * AVG(status = 'Returned'), 1)  AS return_rate_pct
FROM orders GROUP BY payment_method ORDER BY cancel_rate_pct DESC
''')
cancel_city = q('''
SELECT s.city, COUNT(DISTINCT s.order_id) AS orders,
       ROUND(AVG(o.delivery_days), 1) AS avg_delivery_days,
       ROUND(100.0 * COUNT(DISTINCT CASE WHEN s.status='Cancelled' THEN s.order_id END) / COUNT(DISTINCT s.order_id), 1) AS cancel_rate_pct,
       ROUND(SUM(CASE WHEN s.status='Delivered' THEN s.net_revenue ELSE 0 END)) AS delivered_revenue,
       ROUND(SUM(CASE WHEN s.status='Cancelled' THEN s.net_revenue ELSE 0 END)) AS cancelled_revenue
FROM sales s JOIN orders o USING(order_id)
GROUP BY s.city ORDER BY orders DESC
''')
display(cancel_pay, cancel_city)
""")
code("""
cancel_delivery = q('''
SELECT CASE WHEN delivery_days <= 1 THEN '1 day' WHEN delivery_days <= 3 THEN '2–3 days'
            WHEN delivery_days <= 5 THEN '4–5 days' ELSE '6+ days' END AS delivery_bucket,
       COUNT(*) AS orders, ROUND(100.0 * AVG(status = 'Cancelled'), 1) AS cancel_rate_pct
FROM orders GROUP BY delivery_bucket ORDER BY MIN(delivery_days)
''')
lost = q('''
SELECT ROUND(SUM(net_revenue)) AS cancelled_revenue,
       ROUND(100.0 * SUM(net_revenue) / (SELECT SUM(net_revenue) FROM sales WHERE status IN ('Delivered','Cancelled')), 1) AS pct_of_demand,
       ROUND(SUM(CASE WHEN payment_method='Cash on delivery' THEN net_revenue END)) AS cod_cancelled_revenue
FROM sales WHERE status = 'Cancelled'
''')
display(cancel_delivery, lost)
""")
code("""
fig, axes = plt.subplots(1, 2, figsize=(12, 4))
axes[0].bar(cancel_pay["payment_method"], cancel_pay["cancel_rate_pct"],
            color=[RED if m == "Cash on delivery" else GREY for m in cancel_pay["payment_method"]])
axes[0].set_title("Cancellation rate by payment method", loc="left", fontweight="bold")
axes[1].bar(cancel_delivery["delivery_bucket"], cancel_delivery["cancel_rate_pct"], color=ORANGE)
axes[1].set_title("Cancellation rate by delivery time", loc="left", fontweight="bold")
for a, d in zip(axes, [cancel_pay, cancel_delivery]):
    a.yaxis.set_major_formatter(mtick.PercentFormatter(decimals=0))
    for p in a.patches:
        a.annotate(f"{p.get_height():.1f}%", (p.get_x() + p.get_width() / 2, p.get_height()),
                   ha="center", va="bottom", fontsize=9)
fig.tight_layout(); fig.savefig(IMG / "cancellation_drivers.png", bbox_inches="tight"); plt.show()
""")

md("## 7. Customer concentration & repeat purchases — `NTILE` and cumulative share")
code("""
deciles = q('''
WITH cust AS (
  SELECT customer_id, SUM(net_revenue) AS revenue, COUNT(DISTINCT order_id) AS orders
  FROM sales WHERE status = 'Delivered' GROUP BY customer_id
), d AS (SELECT *, NTILE(10) OVER (ORDER BY revenue DESC) AS decile FROM cust)
SELECT decile, COUNT(*) AS customers, ROUND(SUM(revenue)) AS revenue,
       ROUND(AVG(orders), 1) AS avg_orders,
       ROUND(100.0 * SUM(revenue) / SUM(SUM(revenue)) OVER (), 1) AS revenue_share_pct,
       ROUND(100.0 * SUM(SUM(revenue)) OVER (ORDER BY decile) / SUM(SUM(revenue)) OVER (), 1) AS cumulative_pct
FROM d GROUP BY decile ORDER BY decile
''')
deciles
""")
code("""
repeat = q('''
WITH c AS (SELECT customer_id, COUNT(*) AS n FROM orders WHERE status='Delivered' GROUP BY customer_id)
SELECT COUNT(*) AS buyers,
       ROUND(100.0 * AVG(n >= 2), 1) AS repeat_rate_pct,
       ROUND(AVG(n), 2) AS orders_per_buyer
FROM c
''')
gap = q('''
WITH o AS (
  SELECT customer_id, order_date,
         ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY order_date) AS nth,
         julianday(order_date) - julianday(LAG(order_date) OVER (PARTITION BY customer_id ORDER BY order_date)) AS gap_days
  FROM orders WHERE status = 'Delivered'
)
SELECT ROUND(AVG(CASE WHEN nth = 2 THEN gap_days END)) AS avg_days_1st_to_2nd,
       ROUND(AVG(gap_days)) AS avg_days_between_orders
FROM o
''')
channel = q('''
WITH c AS (
  SELECT s.customer_id, s.acquisition_channel, COUNT(DISTINCT s.order_id) AS n, SUM(s.net_revenue) AS rev
  FROM sales s WHERE status='Delivered' GROUP BY s.customer_id, s.acquisition_channel
)
SELECT acquisition_channel, COUNT(*) AS buyers, ROUND(100.0*AVG(n>=2),1) AS repeat_rate_pct,
       ROUND(AVG(rev)) AS revenue_per_buyer
FROM c GROUP BY acquisition_channel ORDER BY revenue_per_buyer DESC
''')
display(repeat, gap, channel)
""")
code("""
fig, axes = plt.subplots(1, 2, figsize=(12, 4.2))
axes[0].bar(deciles["decile"], deciles["revenue_share_pct"], color=BLUE)
axes[0].plot(deciles["decile"], deciles["cumulative_pct"], color=ORANGE, marker="o", lw=2, label="Cumulative share")
axes[0].set_xticks(deciles["decile"]); axes[0].set_xlabel("Customer decile (1 = top 10% by revenue)")
axes[0].yaxis.set_major_formatter(mtick.PercentFormatter(decimals=0)); axes[0].legend(frameon=False, loc="center right")
axes[0].set_title("Revenue concentration by customer decile", loc="left", fontweight="bold")
ch = channel.sort_values("revenue_per_buyer")
axes[1].barh(ch["acquisition_channel"], ch["revenue_per_buyer"], color=GREEN)
for i, (v, r) in enumerate(zip(ch["revenue_per_buyer"], ch["repeat_rate_pct"])):
    axes[1].text(v, i, f"  {v:,.0f} AZN · repeat {r:.0f}%", va="center", fontsize=9)
axes[1].set_xlim(0, ch["revenue_per_buyer"].max() * 1.45)
axes[1].set_title("Revenue per buyer by acquisition channel", loc="left", fontweight="bold")
fig.tight_layout(); fig.savefig(IMG / "customer_concentration.png", bbox_inches="tight"); plt.show()
""")

md("## 8. Findings & recommendations")
code("""
c24, c25 = yearly["revenue"].tolist()
cod = cancel_pay.set_index("payment_method")["cancel_rate_pct"]
top = cat.iloc[0]; elec = cat.set_index("category").loc["Electronics"]
findings = pd.DataFrame([
    ["Growth", f"Revenue {c24:,.0f} → {c25:,.0f} AZN ({yearly['growth_pct'].iloc[1]:+.1f}% YoY); Nov–Dec are the peak months",
     "Plan stock, couriers and ad budget for Novruz, Black Friday and New Year 6–8 weeks ahead"],
    ["Profit mix", f"Electronics = {elec['revenue_share_pct']}% of revenue but only {elec['profit_share_pct']}% of profit ({elec['margin_pct']}% margin); "
                   f"{top['category']} leads profit ({top['profit_share_pct']}%)",
     "Shift promotion spend toward high-margin Fashion & Beauty; use Electronics as traffic driver, not discount target"],
    ["Discounts", f"Lines at 30%+ discount run at {disc.loc[disc['discount_pct']>=30,'margin_pct'].iloc[0]}% margin vs {disc.loc[disc['discount_pct']==0,'margin_pct'].iloc[0]}% at full price",
     "Cap discounts at 20% outside Black Friday; exclude low-margin Electronics/Groceries from deep promos"],
    ["Cancellations", f"Cash on delivery cancels at {cod['Cash on delivery']}% vs {cod['Card']}% for card; {lost['cancelled_revenue'][0]:,.0f} AZN lost",
     "Offer a small card-payment incentive / prepaid deposit for COD orders above a threshold"],
    ["Delivery", f"Cancel rate rises from {cancel_delivery['cancel_rate_pct'].iloc[0]}% (1 day) to {cancel_delivery['cancel_rate_pct'].iloc[-1]}% (6+ days)",
     "Add regional pickup points / partner couriers in Lankaran, Sheki, Ganja"],
    ["Loyalty", f"Top 10% of buyers = {deciles['revenue_share_pct'].iloc[0]}% of revenue; repeat rate {repeat['repeat_rate_pct'][0]}%",
     "Launch a VIP tier for the top decile and a 2nd-order coupon timed ~{:.0f} days after first purchase".format(gap['avg_days_1st_to_2nd'][0])],
], columns=["Area", "Finding", "Recommendation"])
findings.style.hide(axis="index").set_properties(**{"text-align": "left", "white-space": "pre-wrap"})
""")
code("con.close()")

nb["cells"] = cells
nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
nbf.write(nb, "sales_analysis.ipynb")
print("notebook written")
