-- Story queries for one Airspace session.
-- Run without touching the live database:
--   sqlite3 -header -column :memory: < .cursor/docs/session-queries.sql
-- Change the session id in `params` below.

ATTACH 'file:data/airspace.sqlite?mode=ro' AS a;
.import --csv catalogs/los-angeles.csv catalog

CREATE TEMP TABLE params AS
SELECT 16 AS session_id, 34.0045403 AS home_lat, -118.4478041 AS home_lon;

-- Observations with distance from home (miles) and local time
CREATE TEMP VIEW obs AS
SELECT o.*,
       datetime(o.received_at, 'localtime') AS local_at,
       3958.8 * 2 * asin(sqrt(
         pow(sin(radians(o.latitude - p.home_lat) / 2), 2) +
         cos(radians(p.home_lat)) * cos(radians(o.latitude)) *
         pow(sin(radians(o.longitude - p.home_lon) / 2), 2)
       )) AS dist_mi
FROM a.observations o, params p
WHERE o.session_id = p.session_id;


-- 1. The night in numbers
.print ''
.print '== 1. Session summary'
SELECT min(datetime(requested_at, 'localtime')) AS started,
       max(datetime(requested_at, 'localtime')) AS ended,
       round((julianday(max(requested_at)) - julianday(min(requested_at))) * 24, 1) AS hours,
       count(*) AS requests,
       sum(status_code = 200) AS succeeded,
       sum(status_code = 429) AS rate_limited,
       sum(error_kind = 'transient') AS transient_errors,
       sum(fast) AS fast_polls,
       round(avg(latency_ms)) AS avg_latency_ms
FROM a.polls, params p
WHERE polls.session_id = p.session_id AND kind = 'poll';

.print ''
.print '== 1b. Distinct aircraft'
SELECT count(DISTINCT aircraft_id) AS aircraft,
       count(DISTINCT CASE WHEN category = 'A7' THEN aircraft_id END) AS rotorcraft,
       count(DISTINCT CASE WHEN dist_mi < 1 THEN aircraft_id END) AS within_1_mi
FROM obs;


-- 2. Every helicopter: who, when, how close, how low
.print ''
.print '== 2. Rotorcraft roster'
SELECT h.registration,
       coalesce(c.operator_short, h.owner_operator) AS operator,
       c.role,
       h.aircraft_type AS type,
       time(min(h.local_at)) AS first_seen,
       time(max(h.local_at)) AS last_seen,
       round((julianday(max(h.received_at)) - julianday(min(h.received_at))) * 1440) AS span_min,
       round(min(h.dist_mi), 2) AS closest_mi,
       min(CASE WHEN h.on_ground = 0 THEN h.altitude_baro_ft END) AS lowest_ft
FROM obs h
LEFT JOIN catalog c ON lower(c.icao_hex) = h.aircraft_id
WHERE h.category = 'A7'
GROUP BY h.aircraft_id
ORDER BY closest_mi;


-- 3. Closest approach, all aircraft
.print ''
.print '== 3. Ten closest passes'
WITH ranked AS (
  SELECT aircraft_id, registration, callsign, aircraft_type, owner_operator,
         local_at, altitude_baro_ft, ground_speed_kt, dist_mi,
         row_number() OVER (PARTITION BY aircraft_id ORDER BY dist_mi) AS rn
  FROM obs
  WHERE on_ground = 0
)
SELECT registration, trim(callsign) AS callsign, aircraft_type AS type,
       owner_operator, time(local_at) AS at,
       round(dist_mi, 2) AS dist_mi, altitude_baro_ft AS alt_ft,
       round(ground_speed_kt) AS kt
FROM ranked
WHERE rn = 1
ORDER BY dist_mi
LIMIT 10;


-- 4. When the sky was busy
.print ''
.print '== 4. Aircraft by hour (local)'
SELECT strftime('%H:00', local_at) AS hour,
       count(DISTINCT aircraft_id) AS aircraft,
       count(DISTINCT CASE WHEN category = 'A7' THEN aircraft_id END) AS helicopters,
       count(DISTINCT CASE WHEN dist_mi < 2 THEN aircraft_id END) AS within_2_mi
FROM obs
GROUP BY 1
ORDER BY min(received_at);

.print ''
.print '== 4b. Single busiest poll'
SELECT datetime(p.requested_at, 'localtime') AS at,
       count(*) AS aircraft_in_view,
       sum(o.category = 'A7') AS helicopters
FROM a.observations o
JOIN a.polls p ON p.id = o.poll_id
WHERE o.session_id = (SELECT session_id FROM params)
GROUP BY o.poll_id
ORDER BY aircraft_in_view DESC
LIMIT 3;


-- 5. Quietest stretch: longest gap with no helicopter in the feed
.print ''
.print '== 5. Longest helicopter-free stretches'
WITH heli_polls AS (
  SELECT DISTINCT poll_id, received_at FROM obs WHERE category = 'A7'
), gaps AS (
  SELECT received_at,
         lag(received_at) OVER (ORDER BY received_at) AS prev_at
  FROM (SELECT min(received_at) AS received_at FROM heli_polls GROUP BY poll_id)
)
SELECT time(datetime(prev_at, 'localtime')) AS from_,
       time(datetime(received_at, 'localtime')) AS to_,
       round((julianday(received_at) - julianday(prev_at)) * 1440) AS minutes
FROM gaps
WHERE prev_at IS NOT NULL
ORDER BY minutes DESC
LIMIT 5;


-- 6. Helicopter time on station: 10-minute windows where a helicopter
-- stayed inside a small box (a crude stand-in for orbit detection;
-- `airspace report` has the real circling/hovering episodes)
.print ''
.print '== 6. Helicopters loitering (10-min windows, box < 1 km)'
WITH w AS (
  SELECT aircraft_id, registration, owner_operator,
         strftime('%Y-%m-%d %H:', local_at) ||
           printf('%02d', (cast(strftime('%M', local_at) AS int) / 10) * 10) AS window,
         (max(latitude) - min(latitude)) * 111000 AS ns_m,
         (max(longitude) - min(longitude)) * 111000 * cos(radians(avg(latitude))) AS ew_m,
         avg(ground_speed_kt) AS avg_kt,
         avg(altitude_baro_ft) AS avg_ft,
         min(dist_mi) AS min_dist_mi,
         count(*) AS n
  FROM obs
  WHERE category = 'A7' AND on_ground = 0
  GROUP BY aircraft_id, window
  HAVING n >= 30
)
SELECT registration, owner_operator, window,
       round(max(ns_m, ew_m)) AS box_m,
       round(avg_kt) AS avg_kt, round(avg_ft) AS avg_ft,
       round(min_dist_mi, 1) AS dist_mi
FROM w
WHERE max(ns_m, ew_m) < 1000
ORDER BY registration, window;


-- 7. Two or more helicopters slow in the same place at the same time
-- (the "news coverage" pattern): same minute, within 1 km, both under 30 kt
.print ''
.print '== 7. Helicopters stacked over the same spot'
WITH m AS (
  SELECT aircraft_id, registration, owner_operator,
         strftime('%Y-%m-%d %H:%M', local_at) AS minute,
         avg(latitude) AS lat, avg(longitude) AS lon,
         avg(altitude_baro_ft) AS ft, avg(ground_speed_kt) AS kt
  FROM obs
  WHERE category = 'A7' AND on_ground = 0
  GROUP BY aircraft_id, minute
  HAVING kt < 30
)
SELECT x.minute,
       x.registration || ' / ' || y.registration AS pair,
       x.owner_operator || ' / ' || y.owner_operator AS operators,
       round(x.ft) || ' / ' || round(y.ft) AS alt_ft,
       round(6371000 * 2 * asin(sqrt(
         pow(sin(radians(y.lat - x.lat) / 2), 2) +
         cos(radians(x.lat)) * cos(radians(y.lat)) *
         pow(sin(radians(y.lon - x.lon) / 2), 2)))) AS apart_m
FROM m x
JOIN m y ON x.minute = y.minute AND x.aircraft_id < y.aircraft_id
WHERE apart_m < 1000
ORDER BY x.minute;


-- 8. Lowest airborne passes within 3 miles (excluding LAX/SMO traffic
-- on approach is hard in SQL; filter by type or speed if needed)
.print ''
.print '== 8. Lowest airborne aircraft within 3 miles'
WITH ranked AS (
  SELECT *, row_number() OVER (PARTITION BY aircraft_id ORDER BY altitude_baro_ft) AS rn
  FROM obs
  WHERE on_ground = 0 AND dist_mi < 3 AND altitude_baro_ft IS NOT NULL
)
SELECT registration, aircraft_type AS type, category, owner_operator,
       time(local_at) AS at, altitude_baro_ft AS alt_ft,
       round(ground_speed_kt) AS kt, round(dist_mi, 2) AS dist_mi
FROM ranked
WHERE rn = 1
ORDER BY altitude_baro_ft
LIMIT 10;


-- 9. Emergency squawks and odd codes
.print ''
.print '== 9. Emergency squawks'
SELECT registration, trim(callsign) AS callsign, aircraft_type AS type,
       squawk, emergency,
       time(min(local_at)) AS first_at, time(max(local_at)) AS last_at,
       count(*) AS n
FROM obs
WHERE squawk IN ('7500', '7600', '7700') OR (emergency IS NOT NULL AND emergency <> 'none')
GROUP BY aircraft_id, squawk, emergency;


-- 10. The fleet overhead: most common types
.print ''
.print '== 10. Most common aircraft types'
SELECT aircraft_type AS type,
       max(aircraft_description) AS description,
       count(DISTINCT aircraft_id) AS aircraft
FROM obs
WHERE aircraft_type IS NOT NULL
GROUP BY aircraft_type
ORDER BY aircraft DESC
LIMIT 12;
