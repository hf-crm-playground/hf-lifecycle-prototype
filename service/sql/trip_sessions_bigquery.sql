-- Trip sessionisation, core logic, BigQuery Standard SQL.
-- Same logic as trip_sessions_sqlite.sql (which is the tested reference);
-- ported for an hourly scheduled query. Snowflake/Redshift: replace
-- DATE_DIFF/DATE_ADD with DATEDIFF/DATEADD and the DECLAREs with
-- session variables.
--
-- Inputs (replace `holafly.crm.*` with your dataset):
--   holafly.crm.customers(customer_id STRING, home_country STRING)
--   holafly.crm.orders(customer_id STRING, esim_id STRING, status STRING)
--   holafly.crm.usage_daily(customer_id STRING, esim_id STRING, day DATE,
--                           mb_used FLOAT64, network_country STRING)

DECLARE as_of_day DATE DEFAULT CURRENT_DATE('UTC');
DECLARE gap_days INT64 DEFAULT 14;        -- trip ends after 14 days without usage
DECLARE confirm_days INT64 DEFAULT 16;    -- end confirmed after 16 days
DECLARE post_trip_days INT64 DEFAULT 30;  -- post-trip window
DECLARE test_mb FLOAT64 DEFAULT 50.0;     -- home-only day under this = test install

CREATE OR REPLACE TABLE holafly.crm.trip_sessions AS
WITH valid_usage AS (
  SELECT u.customer_id, u.esim_id, u.day, u.mb_used, u.network_country
  FROM holafly.crm.usage_daily u
  LEFT JOIN holafly.crm.orders o
    ON o.customer_id = u.customer_id AND o.esim_id = u.esim_id
  WHERE COALESCE(o.status, 'confirmed') NOT IN ('refunded', 'cancelled')
    AND u.mb_used >= 0
),
day_agg AS (
  SELECT v.customer_id, v.day, c.home_country,
         SUM(v.mb_used) AS mb_day,
         COUNTIF(v.network_country IS NULL) AS unknown_rows,
         COUNTIF(v.network_country IS NOT NULL
                 AND v.network_country != c.home_country) AS away_rows
  FROM valid_usage v
  JOIN holafly.crm.customers c USING (customer_id)
  GROUP BY v.customer_id, v.day, c.home_country
),
kept_days AS (
  SELECT * FROM day_agg
  WHERE NOT (home_country IS NOT NULL AND unknown_rows = 0
             AND away_rows = 0 AND mb_day < test_mb)
),
seq AS (
  SELECT *, LAG(day) OVER (PARTITION BY customer_id ORDER BY day) AS prev_day
  FROM kept_days
),
flagged AS (
  SELECT *,
         IF(prev_day IS NULL OR DATE_DIFF(day, prev_day, DAY) > gap_days, 1, 0) AS new_trip
  FROM seq
),
numbered AS (
  SELECT *,
         SUM(new_trip) OVER (PARTITION BY customer_id ORDER BY day
                             ROWS UNBOUNDED PRECEDING) AS trip_seq
  FROM flagged
),
trips AS (
  SELECT customer_id, trip_seq,
         MIN(day)    AS first_usage_day,
         MAX(day)    AS last_usage_day,
         COUNT(*)    AS usage_days,
         SUM(mb_day) AS mb_used_total,
         MAX(mb_day) AS max_day_mb,
         LOGICAL_OR(away_rows > 0)     AS outside_home,
         LOGICAL_AND(unknown_rows = 0) AS country_known_all_days
  FROM numbered
  GROUP BY customer_id, trip_seq
),
scored AS (
  SELECT *,
         CONCAT(customer_id, ':', CAST(first_usage_day AS STRING)) AS trip_id,
         DATE_ADD(last_usage_day, INTERVAL gap_days DAY)          AS provisional_end_day,
         DATE_DIFF(as_of_day, last_usage_day, DAY)                AS days_since_last_usage,
         ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY last_usage_day DESC) AS recency_rank
  FROM trips
)
SELECT customer_id, trip_id, first_usage_day, last_usage_day, provisional_end_day,
       usage_days, mb_used_total, max_day_mb, outside_home, days_since_last_usage,
       days_since_last_usage > confirm_days AS confirmed,
       recency_rank = 1 AS is_latest,
       CASE
         WHEN days_since_last_usage <= gap_days     THEN 'in_trip'
         WHEN days_since_last_usage <= confirm_days THEN 'trip_end_provisional'
         WHEN DATE_DIFF(as_of_day, provisional_end_day, DAY) <= post_trip_days
                                                    THEN 'post_trip'
         ELSE 'between_trips'
       END AS state_if_latest,
       CASE
         WHEN outside_home THEN 'high'
         WHEN NOT country_known_all_days AND max_day_mb >= 50.0 THEN 'high'
         ELSE 'medium'
       END AS trip_confidence,
       as_of_day
FROM scored;
