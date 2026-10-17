import { describe, expect, it, vi } from 'vitest';
import { aircraft, snapshot } from '../test/fixtures';
import { ApiError, fetchSnapshot, replayTarget, startPolling } from './api';
import { applyFilters, defaultFilters } from './filters';
import { altitude, cardinal, degrees, feet, knots, seconds, verticalRate } from './format';
import { ageAircraft } from './freshness';
import { aircraftCollection, circle, destination, iconFor, rings, trailCollection } from './geo';
import { statusView } from './status';

describe('format', () => {
  it('keeps zero distinct from unknown', () => {
    expect(feet(0)).toBe('0 ft');
    expect(feet(null)).toBe('Unknown');
    expect(knots(0)).toBe('0 kt');
    expect(degrees(0)).toBe('0°');
    expect(degrees(360)).toBe('0°');
    expect(verticalRate(0)).toBe('Level (0 ft/min)');
    expect(verticalRate(-640)).toBe('Descending 640 ft/min');
  });
  it('labels ground separately from altitude', () => {
    expect(altitude({ altitude_baro_ft: null, on_ground: true })).toBe('On ground');
    expect(altitude({ altitude_baro_ft: null, on_ground: null })).toBe('Unknown');
  });
  it('formats ages and bearings', () => {
    expect(seconds(42.4)).toBe('42s');
    expect(seconds(125)).toBe('2 min 5s');
    expect(cardinal(359)).toBe('N');
    expect(cardinal(225)).toBe('SW');
  });
});

describe('filters', () => {
  const list = [
    aircraft({ id: 'low', altitude_baro_ft: 900 }),
    aircraft({ id: 'high', altitude_baro_ft: 12000, class: 'aircraft' }),
    aircraft({ id: 'unk', altitude_baro_ft: null, on_ground: null }),
    aircraft({ id: 'gnd', altitude_baro_ft: null, on_ground: true, class: 'aircraft' }),
    aircraft({ id: 'veh', class: 'surface', on_ground: true, altitude_baro_ft: null }),
  ];
  const ids = (f = defaultFilters()) => applyFilters(list, f).map((a) => a.id);

  it('defaults hide ground traffic and surface vehicles but keep unknown altitude', () => {
    expect(ids()).toEqual(['low', 'high', 'unk']);
  });
  it('altitude preset', () => {
    expect(ids({ ...defaultFilters(), maxAltitudeFt: 10_000 })).toEqual(['low', 'unk']);
    expect(ids({ ...defaultFilters(), maxAltitudeFt: 10_000, includeUnknownAltitude: false })).toEqual(['low']);
  });
  it('opt-in categories', () => {
    expect(ids({ ...defaultFilters(), includeOnGround: true, includeSurface: true })).toHaveLength(5);
  });
});

describe('geo', () => {
  it('destination and circle use [lon, lat]', () => {
    const [lon, lat] = destination([-118.42, 34.0], 0, 1852);
    expect(lon).toBeCloseTo(-118.42, 6);
    expect(lat).toBeCloseTo(34.0 + 1 / 60, 3); // one nautical mile ~ one arcminute
    const ring = circle([-118.42, 34.0], 1000, 16);
    expect(ring).toHaveLength(17);
    expect(ring[0]).toEqual(ring[16]);
  });
  it('rings include each mile, the visible boundary and the collection limit', () => {
    const labels = rings([-118.42, 34.0], 8046.72, 18520)
      .features.filter((f) => f.geometry.type === 'Point' && f.properties.kind !== 'home')
      .map((f) => f.properties.label);
    expect(labels).toEqual(['1 mi', '2 mi', '3 mi', '4 mi', '5 mi', 'Collection limit']);
    const home = rings([-118.42, 34.0], 8046.72, 18520).features.find((f) => f.properties.kind === 'home');
    expect(home?.geometry).toEqual({ type: 'Point', coordinates: [-118.42, 34.0] });
  });
  it('icons: unrotated without a track, helicopter only when classified', () => {
    expect(iconFor({ class: 'rotorcraft', track_deg: 90 })).toBe('heli');
    expect(iconFor({ class: 'rotorcraft', track_deg: null })).toBe('heli-still');
    expect(iconFor({ class: 'unknown', track_deg: 0 })).toBe('plane');
    expect(iconFor({ class: 'aircraft', track_deg: null })).toBe('plane-still');
    expect(iconFor({ class: 'surface', track_deg: 10 })).toBe('surface');
  });
  it('features carry rotation, selection and labels', () => {
    const fc = aircraftCollection([aircraft({ track_deg: 0 }), aircraft({ id: 'b', track_deg: null })], 'b');
    expect(fc.features[0].geometry.coordinates).toEqual([-118.41, 34.01]);
    expect(fc.features[0].properties).toMatchObject({ rotation: 0, icon: 'heli', selected: false, label: 'N225LA\n015' });
    expect(fc.features[1].properties).toMatchObject({ icon: 'heli-still', selected: true });
  });
  it('trail segments stay separate', () => {
    const trail = [
      [[-118.4, 34.0], [-118.41, 34.0]],
      [[-118.45, 34.02], [-118.46, 34.02]],
    ] as [number, number][][];
    const fc = trailCollection([aircraft({ trail }), aircraft({ id: 'none', trail: [] })], null);
    expect(fc.features).toHaveLength(1);
    expect(fc.features[0].geometry).toEqual({ type: 'MultiLineString', coordinates: trail });
  });
});

describe('freshness aging', () => {
  const t = snapshot().thresholds;
  it('fades then hides as the client clock advances', () => {
    const a = [aircraft({ position_age_s: 5 })];
    expect(ageAircraft(a, 0, t)[0].freshness).toBe('fresh');
    expect(ageAircraft(a, 20, t)[0]).toMatchObject({ freshness: 'fading', position_age_s: 25 });
    expect(ageAircraft(a, 56, t)).toEqual([]);
  });
  it('unknown position time hides after the hide threshold', () => {
    const a = [aircraft({ position_age_s: null, freshness: 'unknown' })];
    expect(ageAircraft(a, 30, t)[0].freshness).toBe('unknown');
    expect(ageAircraft(a, 61, t)).toEqual([]);
  });
});

describe('status', () => {
  it('maps each feed state', () => {
    expect(statusView(snapshot(), null, 2)).toMatchObject({ level: 'ok', label: 'Live', detail: 'Feed updated 3s ago' });
    expect(statusView(snapshot({}, { status: 'degraded', last_error: 'HTTP 503' }), null, 0).detail).toContain('HTTP 503');
    const stale = statusView(snapshot({}, { status: 'stale' }), null, 0);
    expect(stale).toMatchObject({ level: 'alert', untrusted: true });
    expect(stale.detail).toContain('does not mean an empty sky');
    expect(statusView(snapshot({}, { status: 'paused', paused: 'HTTP 403' }), null, 0).label).toBe('Collection paused');
  });
  it('a local failure overrides a live-looking snapshot', () => {
    const view = statusView(snapshot(), new ApiError("Can't reach the local server"), 10);
    expect(view).toMatchObject({ level: 'alert', label: 'Local server not responding', untrusted: true });
  });
  it('replay is labeled as replay', () => {
    const s = snapshot({ mode: 'replay', replay: { session_id: 1, at: '2026-10-17T20:00:00Z', session_start: null, session_end: null } });
    expect(statusView(s, null, 0)).toMatchObject({ level: 'info', label: 'Replay: live', untrusted: false });
    const outage = snapshot({ mode: 'replay', replay: s.replay, aircraft: [] }, { status: 'stale' });
    expect(statusView(outage, null, 0)).toMatchObject({ level: 'alert', label: 'Replay: no current data', untrusted: true });
  });
});

describe('api', () => {
  const ok = (body: unknown) => Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));

  it('parses replay targets', () => {
    expect(replayTarget('?session=3&at=2026-10-17T20:00:00Z')).toEqual({ sessionId: 3, at: '2026-10-17T20:00:00Z' });
    expect(replayTarget('?session=x&at=1')).toBeNull();
    expect(replayTarget('')).toBeNull();
  });
  it('builds replay URLs with encoded timestamps', async () => {
    const fetchFn = vi.fn(() => ok(snapshot()));
    await fetchSnapshot({ sessionId: 2, at: '2026-10-17T20:00:00+00:00' }, fetchFn as unknown as typeof fetch);
    expect((fetchFn.mock.calls[0] as unknown[])[0]).toBe('/api/replay?session_id=2&at=2026-10-17T20%3A00%3A00%2B00%3A00');
  });
  it('surfaces server detail and rejects unknown versions', async () => {
    const conflict = () => Promise.resolve(new Response(JSON.stringify({ detail: 'replay mode' }), { status: 409 }));
    await expect(fetchSnapshot(null, conflict as unknown as typeof fetch)).rejects.toMatchObject({ status: 409, message: 'replay mode' });
    await expect(fetchSnapshot(null, (() => ok({ version: 2 })) as unknown as typeof fetch)).rejects.toThrow('Unsupported');
  });
  it('network failures become ApiErrors', async () => {
    const down = () => Promise.reject(new TypeError('fetch failed'));
    await expect(fetchSnapshot(null, down as unknown as typeof fetch)).rejects.toThrow("Can't reach the local server");
  });
  it('polls one request at a time and stops cleanly', async () => {
    vi.useFakeTimers();
    let inFlight = 0;
    let maxInFlight = 0;
    const fetchFn = vi.fn(async () => {
      inFlight++;
      maxInFlight = Math.max(maxInFlight, inFlight);
      await new Promise((r) => setTimeout(r, 3000)); // slower than the poll interval
      inFlight--;
      return new Response(JSON.stringify(snapshot()), { status: 200 });
    });
    const seen: number[] = [];
    const stop = startPolling((s) => seen.push(s.sequence), () => {}, { fetchFn: fetchFn as unknown as typeof fetch, intervalMs: 2000 });
    await vi.advanceTimersByTimeAsync(11_000);
    stop();
    const calls = fetchFn.mock.calls.length;
    await vi.advanceTimersByTimeAsync(10_000);
    vi.useRealTimers();
    expect(maxInFlight).toBe(1);
    expect(calls).toBe(3); // 0-3s, 5-8s, 10s-
    expect(fetchFn.mock.calls.length).toBe(calls);
  });
});
