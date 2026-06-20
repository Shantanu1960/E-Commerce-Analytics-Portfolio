-- =============================================================================
-- queries.sql
-- 8 business questions against the cleaned `ecommerce` Postgres database.
-- Schema: customers, sellers, products, orders, order_items
-- Revenue convention: revenue = SUM(order_items.price) for orders that were
-- actually delivered (canceled/unavailable orders are excluded from revenue
-- questions; included where relevant, e.g. operational/status questions).
-- =============================================================================


-- -----------------------------------------------------------------------------
-- Q1. Monthly revenue trend
-- Business question: Is revenue growing month over month, and where are the
-- peaks/dips?
-- -----------------------------------------------------------------------------
SELECT
    date_trunc('month', o.order_purchase_timestamp)::date AS month,
    ROUND(SUM(oi.price)::numeric, 2)                       AS revenue,
    COUNT(DISTINCT o.order_id)                              AS orders
FROM orders o
JOIN order_items oi ON oi.order_id = o.order_id
WHERE o.order_status = 'delivered'
GROUP BY 1
ORDER BY 1;


-- -----------------------------------------------------------------------------
-- Q2. Month-over-month revenue growth %
-- Business question: How fast is revenue growing/shrinking, in relative terms?
-- -----------------------------------------------------------------------------
WITH monthly AS (
    SELECT
        date_trunc('month', o.order_purchase_timestamp)::date AS month,
        SUM(oi.price) AS revenue
    FROM orders o
    JOIN order_items oi ON oi.order_id = o.order_id
    WHERE o.order_status = 'delivered'
    GROUP BY 1
)
SELECT
    month,
    ROUND(revenue::numeric, 2) AS revenue,
    ROUND(
        (100.0 * (revenue - LAG(revenue) OVER (ORDER BY month))
        / NULLIF(LAG(revenue) OVER (ORDER BY month), 0))::numeric
    , 1) AS mom_growth_pct
FROM monthly
ORDER BY month;


-- -----------------------------------------------------------------------------
-- Q3. Top 10 products by revenue
-- Business question: Which SKUs should we make sure never go out of stock?
-- -----------------------------------------------------------------------------
SELECT
    oi.product_id,
    p.product_category_name,
    ROUND(SUM(oi.price)::numeric, 2) AS revenue,
    COUNT(*)                          AS units_sold
FROM order_items oi
JOIN orders o ON o.order_id = oi.order_id
JOIN products p ON p.product_id = oi.product_id
WHERE o.order_status = 'delivered'
GROUP BY oi.product_id, p.product_category_name
ORDER BY revenue DESC
LIMIT 10;


-- -----------------------------------------------------------------------------
-- Q4. Top categories by revenue and order count
-- Business question: Which categories are actually driving the business, vs.
-- which just have a lot of SKUs?
-- -----------------------------------------------------------------------------
SELECT
    p.product_category_name,
    ROUND(SUM(oi.price)::numeric, 2)        AS revenue,
    COUNT(DISTINCT o.order_id)               AS orders,
    ROUND(AVG(oi.price)::numeric, 2)         AS avg_item_price
FROM order_items oi
JOIN orders o ON o.order_id = oi.order_id
JOIN products p ON p.product_id = oi.product_id
WHERE o.order_status = 'delivered'
GROUP BY p.product_category_name
ORDER BY revenue DESC;


-- -----------------------------------------------------------------------------
-- Q5. Customer retention: % of customers who ordered more than once
-- Business question: Are we building a repeat-purchase customer base, or
-- constantly buying new traffic?
-- IMPORTANT: must group by customer_unique_id, not customer_id -- in this
-- schema customer_id is generated fresh per order/account-creation event,
-- so grouping by customer_id alone would make retention look artificially low.
-- -----------------------------------------------------------------------------
WITH orders_per_person AS (
    SELECT
        c.customer_unique_id,
        COUNT(DISTINCT o.order_id) AS n_orders
    FROM orders o
    JOIN customers c ON c.customer_id = o.customer_id
    WHERE o.order_status = 'delivered'
    GROUP BY c.customer_unique_id
)
SELECT
    COUNT(*) FILTER (WHERE n_orders > 1)                         AS repeat_customers,
    COUNT(*)                                                      AS total_customers,
    ROUND(100.0 * COUNT(*) FILTER (WHERE n_orders > 1) / COUNT(*), 1) AS repeat_rate_pct
FROM orders_per_person;


-- -----------------------------------------------------------------------------
-- Q6. Average order value (AOV) by customer state
-- Business question: Where are our highest-value customers, geographically?
-- -----------------------------------------------------------------------------
WITH order_totals AS (
    SELECT
        o.order_id,
        c.customer_state,
        SUM(oi.price) AS order_value
    FROM orders o
    JOIN customers c ON c.customer_id = o.customer_id
    JOIN order_items oi ON oi.order_id = o.order_id
    WHERE o.order_status = 'delivered'
    GROUP BY o.order_id, c.customer_state
)
SELECT
    customer_state,
    COUNT(*)                          AS orders,
    ROUND(AVG(order_value)::numeric, 2) AS avg_order_value
FROM order_totals
GROUP BY customer_state
ORDER BY avg_order_value DESC;


-- -----------------------------------------------------------------------------
-- Q7. Late delivery rate by seller (top offenders, min. 20 delivered orders)
-- Business question: Which sellers are most responsible for late deliveries
-- and customer complaints?
-- -----------------------------------------------------------------------------
WITH seller_deliveries AS (
    SELECT
        oi.seller_id,
        o.order_id,
        o.order_delivered_customer_date,
        o.order_estimated_delivery_date,
        (o.order_delivered_customer_date > o.order_estimated_delivery_date) AS is_late
    FROM order_items oi
    JOIN orders o ON o.order_id = oi.order_id
    WHERE o.order_status = 'delivered'
      AND o.order_delivered_customer_date IS NOT NULL
)
SELECT
    seller_id,
    COUNT(DISTINCT order_id)                                   AS delivered_orders,
    COUNT(DISTINCT order_id) FILTER (WHERE is_late)            AS late_orders,
    ROUND(100.0 * COUNT(DISTINCT order_id) FILTER (WHERE is_late)
          / COUNT(DISTINCT order_id), 1)                        AS late_rate_pct
FROM seller_deliveries
GROUP BY seller_id
HAVING COUNT(DISTINCT order_id) >= 20
ORDER BY late_rate_pct DESC
LIMIT 15;


-- -----------------------------------------------------------------------------
-- Q8. Average delivery time (days) by state: promised vs. actual
-- Business question: Where is our logistics network underperforming the
-- promise we make customers at checkout?
-- -----------------------------------------------------------------------------
SELECT
    c.customer_state,
    ROUND(AVG(EXTRACT(EPOCH FROM (o.order_delivered_customer_date - o.order_purchase_timestamp)) / 86400)::numeric, 1) AS avg_actual_delivery_days,
    ROUND(AVG(EXTRACT(EPOCH FROM (o.order_estimated_delivery_date - o.order_purchase_timestamp)) / 86400)::numeric, 1) AS avg_promised_delivery_days
FROM orders o
JOIN customers c ON c.customer_id = o.customer_id
WHERE o.order_status = 'delivered'
  AND o.order_delivered_customer_date IS NOT NULL
GROUP BY c.customer_state
ORDER BY avg_actual_delivery_days DESC;
