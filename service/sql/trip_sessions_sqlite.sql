-- Trip sessionisation, core logic, SQLite dialect (window functions need
-- SQLite >= 3.25). This is the executable reference used by
-- tests/test_sql_parity.py; sql/trip_sessions_bigquery.sql is the same
-- logic in BigQuery syntax.
--
-- Inputs
--   customers(customer_id TEXT, home_country TEXT)
--   orders(customer_id TEXT, esim_id TEXT, status TEXT)   -- confirmed|refunded|cancelled
--   usage_daily(customer_id TEXT, esim_id TEXT, day TEXT 'YYYY-MM-DD',
--               mb_used REAL, network_country TEXT)
-- Parameter
--   :as_of_day  'YYYY-MM-DD'  (the run date, UTC)
--
-- Output: one row per trip with its state as of :as_of_day. Pre-trip,
-- pending activations, run-out projection and plan-end flags live in the
-- Python builder (they need orders, activations and entitlements joined).

WITH params AS (
  SELECT :as_of_day AS as_of_day,
         14   AS gap_days,        -- trip ends after 14 days without usage
         16   AS confirm_days,    -- end confirmed after 16 days
         30   AS post_trip_days,  -- post-trip window after the end day
         50.0 AS test_mb          -- home-only day under this = test install
),
valid_usage AS (
  SELECT u.customer_id, u.esim_id, u.day, u.mb_used, u.network_country
  FROM usage_daily u
  LEFT JOIN orders o
    ON o.customer_id = u.customer_id AND o.esim_id = u.esim_id
  WHERE COALESCE(o.status, 'confirmed') NOT IN ('refunded', 'cancelled')
    AND u.mb_used >= 0
),
day_agg AS (
  SELECT v.customer_id, v.day, c.home_country,
         SUM(v.mb_used) AS mb_day,
         SUM(CASE WHEN v.network_country IS NULL THEN 1 ELSE 0 END) AS unknown_rows,
         SUM(CASE WHEN v.network_country IS NOT NULL
                   AND v.network_country <> c.home_country THEN 1 ELSE 0 END) AS away_rows
  FROM valid_usage v
  JOIN customers c ON c.customer_id = v.customer_id
  GROUP BY v.customer_id, v.day, c.home_country
),
kept_days AS (
  -- A home-country-only day under test_mb neither starts nor extends a trip.
  SELECT d.*
  FROM day_agg d, params p
  WHERE NOT (d.home_country IS NOT NULL AND d.unknown_rows = 0
             AND d.away_rows = 0 AND d.mb_day < p.test_mb)
),
seq AS (
  SELECT k.*, LAG(k.day) OVER (PARTITION BY k.customer_id ORDER BY k.day) AS prev_day
  FROM kept_days k
),
flagged AS (
  SELECT s.*,
         CASE WHEN s.prev_day IS NULL
                OR julianday(s.day) - julianday(s.prev_day) > p.gap_days
              THEN 1 ELSE 0 END AS new_trip
  FROM seq s, params p
),
numbered AS (
  SELECT f.*,
         SUM(f.new_trip) OVER (PARTITION BY f.customer_id ORDER BY f.day
                               ROWS UNBOUNDED PRECEDING) AS trip_seq
  FROM flagged f
),
trips AS (
  SELECT customer_id, trip_seq,
         MIN(day)      AS first_usage_day,
         MAX(day)      AS last_usage_day,
         COUNT(*)      AS usage_days,
         SUM(mb_day)   AS mb_used_total,
         MAX(mb_day)   AS max_day_mb,
         MAX(away_rows) > 0    AS outside_home,
         MIN(unknown_rows = 0) AS country_known_all_days
  FROM numbered
  GROUP BY customer_id, trip_seq
),
scored AS (
  SELECT t.*,
         t.customer_id || ':' || t.first_usage_day              AS trip_id,
         date(t.last_usage_day, '+' || p.gap_days || ' days')   AS provisional_end_day,
         CAST(julianday(p.as_of_day) - julianday(t.last_usage_day) AS INTEGER) AS days_since_last_usage,
         ROW_NUMBER() OVER (PARTITION BY t.customer_id ORDER BY t.last_usage_day DESC) AS recency_rank,
         p.gap_days, p.confirm_days, p.post_trip_days, p.as_of_day
  FROM trips t, params p
)
SELECT customer_id, trip_id, first_usage_day, last_usage_day, provisional_end_day,
       usage_days, mb_used_total, max_day_mb, outside_home, days_since_last_usage,
       days_since_last_usage > confirm_days AS confirmed,
       recency_rank = 1 AS is_latest,
       CASE
         WHEN days_since_last_usage <= gap_days     THEN 'in_trip'
         WHEN days_since_last_usage <= confirm_days THEN 'trip_end_provisional'
         WHEN julianday(as_of_day) - julianday(provisional_end_day) <= post_trip_days
                                                    THEN 'post_trip'
         ELSE 'between_trips'
       END AS state_if_latest,
       CASE
         WHEN outside_home = 1 THEN 'high'
         WHEN country_known_all_days = 0 AND max_day_mb >= 50.0 THEN 'high'
         ELSE 'medium'
       END AS trip_confidence
FROM scored
ORDER BY customer_id, first_usage_day;
