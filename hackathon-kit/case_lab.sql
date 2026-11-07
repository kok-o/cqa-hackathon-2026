-- All inputs below are synthetic. Replace the fixture tables with typed local views.
SET TimeZone = 'UTC';

CREATE TEMP TABLE events (
    client_id VARCHAR, application_id VARCHAR, step VARCHAR, event_at TIMESTAMPTZ
);
INSERT INTO events VALUES
    ('0001', 'a', 'signup',  '2026-01-01 09:00:00+00'),
    ('0001', 'a', 'verify',  '2026-01-01 10:00:00+00'),
    ('0001', 'a', 'deposit', '2026-01-02 10:00:00+00'),
    ('0001', 'a', 'trade',   '2026-01-03 10:00:00+00'),
    ('0002', 'a', 'signup',  '2026-01-01 09:00:00+00'),
    ('0002', 'a', 'deposit', '2026-01-01 09:15:00+00'),
    ('0002', 'a', 'verify',  '2026-01-01 10:00:00+00'),
    ('0002', 'b', 'deposit', '2026-01-01 11:00:00+00'),
    ('0003', 'a', 'signup',  '2026-01-01 09:00:00+00'),
    ('0003', 'a', 'verify',  '2026-01-01 09:00:00+00'),
    ('0003', 'a', 'verify',  '2026-01-01 10:00:00+00'),
    ('0003', 'a', 'deposit', '2026-01-05 10:00:00+00'),
    ('0003', 'a', 'trade',   '2026-01-08 09:00:00+00'),
    ('0004', 'a', 'signup',  '2026-01-12 09:00:00+00'),
    ('0004', 'a', 'verify',  '2026-01-12 10:00:00+00');

-- A strict funnel per (client_id, application_id), with a single [signup, signup+7d) window.
-- Each step is the nearest later match after the previous step.
-- Ties are deliberately excluded. For tied timestamps, add an event sequence as a tie-breaker.
-- The observation boundary is coverage from the source, not max(event_at).
WITH settings AS (
    SELECT TIMESTAMPTZ '2026-01-01 00:00:00+00' AS cohort_from,
           TIMESTAMPTZ '2026-02-01 00:00:00+00' AS cohort_to,
           TIMESTAMPTZ '2026-01-14 00:00:00+00' AS observed_until,
           INTERVAL '7 days' AS horizon
), first_signup AS (
    SELECT client_id, application_id, MIN(event_at) AS signup_at
    FROM events
    WHERE step = 'signup' AND client_id IS NOT NULL AND application_id IS NOT NULL
    GROUP BY client_id, application_id
), cohort AS (
    SELECT client_id, application_id, signup_at, signup_at + horizon AS deadline
    FROM first_signup CROSS JOIN settings
    WHERE signup_at >= cohort_from AND signup_at < cohort_to
      AND signup_at + horizon <= observed_until
), verification AS (
    SELECT * FROM events WHERE step = 'verify'
), deposit AS (
    SELECT * FROM events WHERE step = 'deposit'
), trade AS (
    SELECT * FROM events WHERE step = 'trade'
)
SELECT 'ordered_funnel' AS check_name,
       COUNT(*) AS signup,
       COUNT(*) FILTER (WHERE v.event_at < c.deadline) AS verify,
       COUNT(*) FILTER (WHERE d.event_at < c.deadline) AS deposit,
       COUNT(*) FILTER (WHERE t.event_at < c.deadline) AS trade
FROM cohort c
ASOF LEFT JOIN verification v
  ON c.client_id = v.client_id AND c.application_id = v.application_id AND c.signup_at < v.event_at
ASOF LEFT JOIN deposit d
  ON v.client_id = d.client_id AND v.application_id = d.application_id AND v.event_at < d.event_at
ASOF LEFT JOIN trade t
  ON d.client_id = t.client_id AND d.application_id = t.application_id AND d.event_at < t.event_at;

CREATE TEMP TABLE ledger (
    client_id VARCHAR, order_id VARCHAR, currency VARCHAR, kind VARCHAR,
    event_at TIMESTAMPTZ, available_at TIMESTAMPTZ, amount_minor BIGINT
);
INSERT INTO ledger VALUES
    ('0001', 'p1', 'KZT', 'buy',    '2026-01-01 09:00:00+00', '2026-01-01 10:00:00+00', 10000),
    ('0001', 'p1', 'KZT', 'buy',    '2026-01-01 09:01:00+00', '2026-01-01 10:00:00+00', 15000),
    ('0001', 'p1', 'KZT', 'return', '2026-01-02 09:00:00+00', '2026-01-02 10:00:00+00', -3000),
    ('0001', 'p2', 'USD', 'buy',    '2026-01-03 09:00:00+00', '2026-01-03 10:00:00+00',  4500),
    ('0001', 'p3', 'KZT', 'buy',    '2026-01-04 09:00:00+00', '2026-01-20 10:00:00+00', 10000),
    ('0002', 'p4', 'KZT', 'buy',    '2026-01-06 09:00:00+00', '2026-01-06 10:00:00+00',  5000);

-- RFM per currency as known on the decision date.
-- Multiple fills contribute to money but only one order to frequency.
-- Returns are signed. Define how reversals affect frequency in the real case.
WITH config AS (SELECT TIMESTAMPTZ '2026-01-10 00:00:00+00' AS cutoff)
SELECT 'rfm' AS check_name, client_id, currency,
       DATE_DIFF('day', MAX(event_at) FILTER (WHERE kind = 'buy'), MAX(cutoff)) AS recency_days,
       COUNT(DISTINCT order_id) FILTER (WHERE kind = 'buy') AS frequency,
       SUM(amount_minor)::HUGEINT AS monetary_minor
FROM ledger CROSS JOIN config
WHERE event_at < cutoff AND available_at <= cutoff
GROUP BY client_id, currency
ORDER BY client_id, currency;

CREATE TEMP TABLE client_dimension (client_id VARCHAR, segment VARCHAR);
INSERT INTO client_dimension VALUES ('0001', 'standard'), ('0001', 'priority'), ('0002', 'standard');

-- Estimate join fanout from key counts, before joining the fact rows.
-- Here the history dimension is deliberately not unique on client_id.
WITH facts AS (SELECT client_id, COUNT(*) AS n FROM ledger GROUP BY client_id),
     dims AS (SELECT client_id, COUNT(*) AS n FROM client_dimension GROUP BY client_id)
SELECT 'join_cardinality' AS check_name, SUM(f.n)::HUGEINT AS fact_rows,
       SUM(f.n::HUGEINT * COALESCE(d.n, 1)) AS left_join_rows,
       COALESCE(SUM(f.n) FILTER (WHERE d.n IS NULL), 0) AS unmatched_fact_rows,
       COUNT(*) FILTER (WHERE d.n > 1) AS fact_keys_with_multiple_matches
FROM facts f LEFT JOIN dims d USING (client_id);
