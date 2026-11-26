import { cleanup, fireEvent, render, screen, within } from '@testing-library/svelte';
import { tick } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Outlines, Snapshot, Weather } from './lib/types';
import { aircraft, snapshot } from './test/fixtures';
import { AttributionControl, instances } from './test/mapbox-mock';

vi.mock('mapbox-gl', () => import('./test/mapbox-mock'));
vi.mock('mapbox-gl/dist/mapbox-gl.css', () => ({}));
// jsdom has no canvas; icons are exercised in the browser
vi.mock('./lib/icons', () => ({
  ICON_NAMES: ['plane', 'plane-still', 'heli', 'heli-still', 'surface'],
  ICON_PIXEL_RATIO: 2,
  drawIcon: () => ({ width: 64, height: 64, data: new Uint8ClampedArray(64 * 64 * 4) }),
}));

const { default: App } = await import('./App.svelte');

type Step = Snapshot | 'down';

const NO_OUTLINES: Outlines = { type: 'FeatureCollection', features: [] };

/** Fake fetch that serves one step per request, repeating the last. Outlines don't use a step. */
function server(steps: Step[], outlines: Outlines = NO_OUTLINES) {
  let i = 0;
  const fetchFn = vi.fn(async (input: RequestInfo | URL) => {
    if (String(input) === '/api/outlines') return new Response(JSON.stringify(outlines), { status: 200 });
    const step = steps[Math.min(i++, steps.length - 1)];
    if (step === 'down') throw new TypeError('fetch failed');
    return new Response(JSON.stringify(step), { status: 200 });
  });
  return fetchFn as unknown as typeof fetch & ReturnType<typeof vi.fn>;
}

const POLL = 2000;

async function advance(ms: number) {
  await vi.advanceTimersByTimeAsync(ms);
  await tick();
}

function map() {
  return instances[instances.length - 1];
}

beforeEach(() => {
  instances.length = 0;
  localStorage.clear();
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date'] });
  vi.setSystemTime(new Date('2026-10-17T20:00:02Z'));
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const heli = aircraft();
const jet = aircraft({
  id: 'a6ed7f', registration: 'N54543', callsign: 'UAL2113', class: 'aircraft', category: 'A3',
  description: 'AIRBUS A-321neo', altitude_baro_ft: 2550, distance_m: 6000, track_deg: 250,
});

describe('App', () => {
  it('lists aircraft by distance and keeps the selection across updates', async () => {
    const moved = { ...heli, longitude: -118.4, altitude_baro_ft: 1700, distance_m: 1800 };
    const fetchFn = server([
      snapshot({ aircraft: [heli, jet] }),
      snapshot({ sequence: 2, aircraft: [moved, jet] }),
      snapshot({ sequence: 3, aircraft: [{ ...moved, distance_m: 7000 }, jet] }),
    ]);
    render(App, { fetchFn, pollMs: POLL });
    await advance(0);

    const rows = within(screen.getByTestId('aircraft-list')).getAllByRole('button');
    expect(rows.map((r) => r.dataset.id)).toEqual(['a1f48f', 'a6ed7f']);
    expect(screen.getByRole('heading', { level: 2, name: /2 aircraft Within 5\.0 mi of home/i })).toBeInTheDocument();

    await fireEvent.click(rows[0]);
    expect(map().panTo).not.toHaveBeenCalled();
    expect(screen.getByTestId('detail')).toHaveTextContent('N225LA');
    expect(screen.getByTestId('detail')).toHaveTextContent('1,500 ft');
    expect(screen.getByTestId('detail')).toHaveTextContent('Helicopter (ADS-B emitter category)');

    await advance(POLL);
    expect(screen.getByTestId('detail')).toHaveTextContent('1,700 ft');
    await advance(POLL);
    // Reordered by distance; the selection follows the aircraft, not the row
    const reordered = within(screen.getByTestId('aircraft-list')).getAllByRole('button');
    expect(reordered.map((r) => r.dataset.id)).toEqual(['a6ed7f', 'a1f48f']);
    expect(reordered[1]).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByTestId('detail')).toHaveTextContent('N225LA');
  });

  it('pans to an offscreen aircraft on row selection and keeps the view on subsequent polls', async () => {
    const moved = { ...jet, longitude: jet.longitude + 0.01 };
    render(App, { fetchFn: server([snapshot({ aircraft: [jet] }), snapshot({ aircraft: [moved] })]), pollMs: POLL });
    await advance(0);
    const row = screen.getByRole('button', { name: /UAL2113/ });
    const m = map();
    m.boundsContain.mockReturnValue(false);

    await fireEvent.click(row);
    expect(m.panTo).toHaveBeenCalledWith([jet.longitude, jet.latitude], { duration: 450 });

    await advance(POLL);
    expect(m.panTo).toHaveBeenCalledTimes(1);
    await fireEvent.click(row);
    expect(m.panTo).toHaveBeenLastCalledWith([moved.longitude, moved.latitude], { duration: 450 });
  });

  it('titles the page from the config', async () => {
    const base = snapshot();
    const fetchFn = server([snapshot({ area: { ...base.area, home: { ...base.area.home, title: "Jim's Airspace" } } })]);
    render(App, { fetchFn, pollMs: POLL });
    await advance(0);
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent("Jim's Airspace");
    expect(document.title).toBe("Jim's Airspace");
  });

  it('creates map layers once and only replaces data on updates', async () => {
    const fetchFn = server([snapshot({ aircraft: [heli, jet] })]);
    render(App, { fetchFn, pollMs: POLL });
    await advance(0);
    const m = map();
    const layers = m.addLayerCalls;
    const sources = m.addSourceCalls;
    expect(m.layers).toEqual(expect.arrayContaining(['rings', 'trails', 'aircraft', 'selection']));
    const helispot = m.getSource('69-bravo')!;
    expect(helispot.spec).toEqual({
      type: 'geojson',
      data: {
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [-118.6273692, 34.074326] },
        properties: { name: '69 Bravo' },
      },
    });
    expect(m.layers).toEqual(expect.arrayContaining(['69-bravo-marker', '69-bravo-label']));
    expect(m.layers.indexOf('69-bravo-label')).toBeLessThan(m.layers.indexOf('aircraft'));

    for (let i = 0; i < 5; i++) await advance(POLL);
    expect(m.addLayerCalls).toBe(layers);
    expect(m.addSourceCalls).toBe(sources);
    expect(helispot.setData).not.toHaveBeenCalled();
    expect(instances).toHaveLength(1);
    expect(m.getSource('aircraft')!.setData.mock.calls.length).toBeGreaterThan(5);

    const last = m.getSource('aircraft')!.setData.mock.calls.at(-1)![0];
    expect(last.features.map((f: { properties: { id: string } }) => f.properties.id)).toEqual(['a1f48f', 'a6ed7f']);
    expect(last.features[1].properties.rotation).toBe(250);
  });

  it('map clicks select an aircraft', async () => {
    render(App, { fetchFn: server([snapshot({ aircraft: [heli, jet] })]), pollMs: POLL });
    await advance(0);
    map().rendered = [{ properties: { id: 'a6ed7f' } }];
    map().fire('click', { point: { x: 1, y: 1 } });
    await tick();
    expect(screen.getByTestId('detail')).toHaveTextContent('UAL2113');
    map().rendered = [];
    map().fire('click', { point: { x: 2, y: 2 } });
    await tick();
    expect(screen.queryByTestId('detail')).toBeNull();
  });

  it('shows each feed status and never an empty sky during outages', async () => {
    const fetchFn = server([
      snapshot(),
      snapshot({ sequence: 2 }, { status: 'degraded', last_error: 'HTTP 503', consecutive_failures: 1 }),
      snapshot({ sequence: 3, aircraft: [] }, { status: 'stale', last_error: 'HTTP 503', last_success_age_s: 75 }),
      snapshot({ sequence: 4, aircraft: [] }, { status: 'paused', paused: 'HTTP 403; client errors count toward the limit' }),
    ]);
    render(App, { fetchFn, pollMs: POLL });
    await advance(0);
    const status = () => screen.getByTestId('feed-status');
    expect(status()).toHaveTextContent('Live');

    await advance(POLL);
    expect(status()).toHaveTextContent('Retrying');
    expect(status()).toHaveTextContent('HTTP 503');

    await advance(POLL);
    expect(status()).toHaveTextContent('No current data');
    expect(status()).toHaveTextContent('does not mean an empty sky');
    expect(screen.getByTestId('list-empty')).toHaveTextContent('No current positions');
    expect(screen.queryByTestId('count')).toBeNull();

    await advance(POLL);
    expect(status()).toHaveTextContent('Collection paused');
    expect(status()).toHaveTextContent('HTTP 403');
  });

  it('ages positions out when the local server stops responding', async () => {
    const fetchFn = server([snapshot({ aircraft: [{ ...heli, position_age_s: 2 }] }), 'down']);
    render(App, { fetchFn, pollMs: POLL });
    await advance(0);
    expect(screen.getByTestId('aircraft-list')).toBeInTheDocument();

    await advance(POLL);
    expect(screen.getByTestId('feed-status')).toHaveTextContent('Local server not responding');

    await advance(25_000);
    expect(screen.getByRole('button', { name: /N225LA/ })).toHaveClass('fading');

    await advance(40_000);
    expect(screen.queryByTestId('aircraft-list')).toBeNull();
    expect(screen.getByTestId('list-empty')).toHaveTextContent('No current positions');
  });

  it('filters change the display without extra requests', async () => {
    const fetchFn = server([snapshot({ aircraft: [heli, { ...jet, altitude_baro_ft: 12000 }] })]);
    render(App, { fetchFn, pollMs: 60_000 });
    await advance(0);
    const requests = fetchFn.mock.calls.length;

    // Svelte binds each option's value, so select by index
    const select = screen.getByTestId('altitude-filter') as HTMLSelectElement;
    select.selectedIndex = 1;
    await fireEvent.change(select);
    const rows = within(screen.getByTestId('aircraft-list')).getAllByRole('button');
    expect(rows.map((r) => r.dataset.id)).toEqual(['a1f48f']);
    expect(fetchFn.mock.calls.length).toBe(requests);
  });

  it('keeps a filtered-out selection visible in the detail panel', async () => {
    render(App, { fetchFn: server([snapshot({ aircraft: [heli, jet] })]), pollMs: 60_000 });
    await advance(0);
    await fireEvent.click(screen.getByRole('button', { name: /UAL2113/ }));
    expect(screen.getByTestId('detail')).not.toHaveTextContent('Hidden from the map');
    const select = screen.getByTestId('altitude-filter') as HTMLSelectElement;
    select.selectedIndex = 3; // below 2,500 ft; the jet is at 2,550
    await fireEvent.change(select);
    expect(screen.queryByRole('button', { name: /UAL2113/ })).toBeNull();
    expect(screen.getByTestId('detail')).toHaveTextContent('UAL2113');
    expect(screen.getByTestId('detail')).toHaveTextContent('Hidden from the map and list by the current filters');
  });

  it('a selected aircraft that drops out shows last seen', async () => {
    const lost = {
      id: heli.id, registration: heli.registration, callsign: heli.callsign, class: heli.class,
      operator_short: null, role: null,
      longitude: heli.longitude, latitude: heli.latitude, position_at: '2026-10-17T20:00:00.000+00:00',
      last_received_at: '2026-10-17T20:00:01.000+00:00', inside_visible: true,
    };
    const fetchFn = server([snapshot({ aircraft: [heli] }), snapshot({ sequence: 2, aircraft: [], last_seen: [lost] })]);
    render(App, { fetchFn, pollMs: POLL });
    await advance(0);
    await fireEvent.click(screen.getByRole('button', { name: /N225LA/ }));
    await advance(POLL);
    expect(screen.getByTestId('detail')).toHaveTextContent('No current position');
    expect(screen.getByTestId('detail')).toHaveTextContent('Last seen');
  });

  it('shows a clear message when the server is unreachable from the start', async () => {
    render(App, { fetchFn: server(['down']), pollMs: POLL });
    await advance(0);
    expect(screen.getByTestId('feed-status')).toHaveTextContent('No connection');
    expect(screen.getByTestId('feed-status')).toHaveTextContent('airspace serve');
  });
});

describe('Replay mode', () => {
  const T = Date.parse('2026-10-17T20:00:00Z');
  const tl = {
    session_id: 1, start: T, end: T + 30_000,
    polls: { t: [T, T + 10_000, T + 20_000, T + 30_000], ok: [true, true, true, true], aircraft: [1, 1, 1, 1], rotorcraft: [1, 1, 1, 1] },
    gaps: [],
  };
  const sessions = [{ id: 1, provider: 'adsbfi', mode: 'collect', started_at: '', ended_at: null, end_reason: null,
                      polls: 4, first_poll_at: null, last_poll_at: null }];

  function router(liveStatus = 200, tvDisplay = false) {
    const calls: string[] = [];
    const fn = vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input), 'http://localhost');
      calls.push(url.pathname + url.search);
      const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
      if (url.pathname === '/api/state') {
        return liveStatus === 200 ? json(snapshot({ session_id: 1 })) : json({ detail: 'replay mode' }, liveStatus);
      }
      if (url.pathname === '/api/sessions') return json(sessions);
      if (url.pathname === '/api/sessions/1/timeline') return json(tl);
      if (url.pathname === '/api/replay') {
        const at = url.searchParams.get('at')!;
        const area = { ...snapshot().area, tv_display: tvDisplay };
        return json(snapshot({ area, mode: 'replay', replay: { session_id: 1, at, session_start: null, session_end: null } }));
      }
      return json({ detail: 'not found' }, 404);
    });
    return { fetchFn: fn as unknown as typeof fetch, calls };
  }

  afterEach(() => window.history.replaceState(null, '', '/'));

  it('switches to replay automatically when the server is in replay mode', async () => {
    const { fetchFn, calls } = router(409);
    render(App, { fetchFn, pollMs: POLL });
    await advance(0);
    await advance(0);
    expect(screen.getByTestId('timeline')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Live' })).toBeNull();
    expect(screen.getByTestId('feed-status')).toHaveTextContent('Replay: live');
    expect(calls.filter((c) => c.startsWith('/api/state'))).toHaveLength(1); // stops polling live state
    expect(window.location.search).toContain('session=1');
  });

  it('toggles between live and replay, plays with the keyboard and syncs the URL', async () => {
    const { fetchFn, calls } = router();
    render(App, { fetchFn, pollMs: POLL, replayTickMs: 250 });
    await advance(0);
    expect(screen.queryByTestId('timeline')).toBeNull();

    await fireEvent.click(screen.getByRole('button', { name: 'Replay' }));
    await advance(0);
    await advance(0);
    expect(screen.getByTestId('timeline')).toBeInTheDocument();
    expect(new URLSearchParams(window.location.search).get('at')).toBe('2026-10-17T20:00:30.000Z');
    const liveCalls = calls.filter((c) => c === '/api/state').length;

    // Space restarts from the beginning, since the playhead is at the end
    await fireEvent.keyDown(window, { key: ' ' });
    expect(screen.getByTestId('play')).toHaveAttribute('aria-label', 'Pause');
    await advance(1000);
    await fireEvent.keyDown(window, { key: ' ' });
    expect(screen.getByTestId('play')).toHaveAttribute('aria-label', 'Play');
    expect(new URLSearchParams(window.location.search).get('at')).toBe('2026-10-17T20:00:16.000Z');
    expect(calls.filter((c) => c === '/api/state').length).toBe(liveCalls); // no live polling while replaying

    await fireEvent.click(screen.getByRole('button', { name: 'Live' }));
    await advance(0);
    expect(screen.queryByTestId('timeline')).toBeNull();
    expect(window.location.search).toBe('');
    expect(calls.filter((c) => c === '/api/state').length).toBeGreaterThan(liveCalls);
  });

  it('opens a replay link at the requested moment with controls even if TV display is configured', async () => {
    window.history.replaceState(null, '', '/?session=1&at=2026-10-17T20:00:10Z');
    const { fetchFn, calls } = router(200, true);
    render(App, { fetchFn, pollMs: POLL });
    await advance(0);
    await advance(0);
    expect(calls).toContain('/api/replay?session_id=1&at=2026-10-17T20%3A00%3A10.000Z');
    expect(calls.some((c) => c === '/api/state')).toBe(false);
    expect(screen.getByTestId('scrubber')).toHaveAttribute('aria-valuenow', String(T + 10_000));
    expect(map().options.interactive).toBe(true);
    expect(screen.getByRole('button', { name: /recenter/i })).toBeInTheDocument();
  });

  it('labels use the style font and the detail panel links to an export', async () => {
    const { fetchFn } = router();
    render(App, { fetchFn, pollMs: POLL });
    await advance(0);
    const aircraftLayer = map().layerSpecs.find((l) => l.id === 'aircraft');
    expect(aircraftLayer?.layout?.['text-font']).toEqual(['Inter Medium', 'Arial Unicode MS Regular']);
    await fireEvent.click(screen.getByRole('button', { name: /N225LA/ }));
    const link = screen.getByRole('link', { name: /Export this aircraft/ });
    expect(link).toHaveAttribute('href', '/api/sessions/1/export?format=geojson&geometry=tracks&aircraft=a1f48f');
  });
});

describe('Map extras', () => {
  it('links row hover and keyboard focus to map highlights without moving the camera', async () => {
    render(App, { fetchFn: server([snapshot({ aircraft: [heli, jet] })]), pollMs: POLL });
    await advance(0);
    const m = map();
    const homeFits = m.fitBounds.mock.calls.length;
    const list = within(screen.getByTestId('aircraft-list'));
    const heliRow = list.getByRole('button', { name: /N225LA/ });
    const jetRow = list.getByRole('button', { name: /UAL2113/ });
    const highlights = (sourceId: string) =>
      m.getSource(sourceId)!.setData.mock.calls.at(-1)![0].features
        .filter((f: { properties: { highlighted: boolean } }) => f.properties.highlighted)
        .map((f: { properties: { id: string } }) => f.properties.id);

    await fireEvent.focus(heliRow);
    expect(highlights('aircraft')).toEqual([heli.id]);
    expect(highlights('trails')).toEqual([heli.id]);

    await fireEvent.pointerEnter(jetRow);
    expect(highlights('aircraft')).toEqual([jet.id]);
    await fireEvent.pointerLeave(jetRow);
    expect(highlights('aircraft')).toEqual([heli.id]);

    m.fire('mousemove:aircraft', { features: [{ properties: { id: jet.id } }] });
    await tick();
    expect(jetRow).toHaveClass('highlighted');
    expect(heliRow).not.toHaveClass('highlighted');
    m.fire('mouseleave:aircraft', {});
    await tick();
    expect(jetRow).not.toHaveClass('highlighted');

    await fireEvent.blur(heliRow);
    expect(highlights('aircraft')).toEqual([]);
    expect(m.panTo).not.toHaveBeenCalled();
    expect(m.fitBounds).toHaveBeenCalledTimes(homeFits);
  });

  it('adds mile labels when the map has room for them', async () => {
    render(App, { fetchFn: server([snapshot()]), pollMs: POLL });
    await advance(0);
    const m = map();
    expect(m.layerSpecs.find(({ id }) => id === 'ring-labels')).toMatchObject({
      filter: ['all', expect.any(Array), ['==', ['get', 'major'], true]],
    });

    m.projectScale = 5000;
    m.fire('zoomend', {});
    expect(m.setFilter.mock.lastCall?.[0]).toBe('ring-labels');
    expect(JSON.stringify(m.setFilter.mock.lastCall?.[1])).not.toContain('"major"');
  });

  it('shows a hover card that follows its aircraft and clears on leave', async () => {
    const moved = { ...heli, longitude: -118.4, vertical_rate_fpm: 640 };
    render(App, { fetchFn: server([snapshot({ aircraft: [heli, jet] }), snapshot({ sequence: 2, aircraft: [moved, jet] })]), pollMs: POLL });
    await advance(0);
    expect(screen.queryByTestId('map-tooltip')).toBeNull();

    map().fire('mousemove:aircraft', { features: [{ properties: { id: 'a1f48f' } }] });
    await tick();
    const card = screen.getByTestId('map-tooltip');
    expect(card).toHaveTextContent('N225LA');
    expect(card).toHaveTextContent('AEROSPATIALE AS-350 Ecureuil');
    expect(card).toHaveTextContent('1,500 ft · 80 kt');
    expect(card).toHaveTextContent('0.9 mi NE of home');
    expect(card.style.left).toBe('90px');

    await advance(POLL);
    expect(screen.getByTestId('map-tooltip')).toHaveTextContent('1,500 ft ↑ · 80 kt');
    expect(screen.getByTestId('map-tooltip').style.left).toBe('100px');

    map().fire('mouseleave:aircraft', {});
    await tick();
    expect(screen.queryByTestId('map-tooltip')).toBeNull();
  });

  it('starts with a collapsed legend that names the home radius and remembers being opened', async () => {
    localStorage.setItem('airspace:legend', '1'); // older dashboards saved their open default
    render(App, { fetchFn: server([snapshot()]), pollMs: POLL });
    await advance(0);
    const legend = screen.getByTestId('legend') as HTMLDetailsElement;
    expect(legend.open).toBe(false);
    expect(legend).toHaveTextContent(/Home radius, \d/);
    expect(legend).toHaveTextContent('Helicopter');
    legend.open = true;
    await fireEvent(legend, new Event('toggle'));
    expect(localStorage.getItem('airspace:legend-expanded')).toBe('1');
    await fireEvent.click(screen.getByTestId('compact-toggle'));
    await fireEvent.click(screen.getByTestId('compact-toggle'));
    expect(screen.getByTestId('legend')).toHaveAttribute('open');
  });

  it('compact mode keeps the map and credit, drops the panel and pins the selection card', async () => {
    const fetchFn = server([snapshot({ aircraft: [heli, jet] })]);
    render(App, { fetchFn, pollMs: POLL });
    await advance(0);
    expect(screen.getByTestId('aircraft-list')).toBeInTheDocument();

    await fireEvent.click(screen.getByTestId('compact-toggle'));
    expect(screen.queryByTestId('aircraft-list')).toBeNull();
    expect(screen.getByTestId('map')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'adsb.fi' })).toHaveAttribute('href', 'https://adsb.fi');
    expect(screen.getByTestId('feed-status')).toBeInTheDocument();
    expect(localStorage.getItem('airspace:compact')).toBe('1');
    // Folded by default in compact
    expect((screen.getByTestId('legend') as HTMLDetailsElement).open).toBe(false);

    map().rendered = [{ properties: { id: 'a6ed7f' } }];
    map().fire('click', { point: { x: 1, y: 1 } });
    await tick();
    expect(screen.getByTestId('map-tooltip')).toHaveTextContent('UAL2113');

    await fireEvent.click(screen.getByTestId('compact-toggle'));
    expect(screen.getByTestId('detail')).toHaveTextContent('UAL2113');
    expect(screen.queryByTestId('map-tooltip')).toBeNull();
    // Same map instance throughout
    expect(instances).toHaveLength(1);
  });

  it('starts compact when the preference says so', async () => {
    localStorage.setItem('airspace:compact', '1');
    render(App, { fetchFn: server([snapshot()]), pollMs: POLL });
    await advance(0);
    expect(screen.queryByTestId('aircraft-list')).toBeNull();
    expect(screen.getByTestId('compact-toggle')).toHaveAttribute('aria-pressed', 'true');
  });
});

describe('Operators', () => {
  const lapd = aircraft({
    operator: 'LAPD Air Support Division', operator_short: 'LAPD', role: 'police', role_rule: 'catalog_hex',
    role_source: 'FAA registry owner', role_checked: '2026-10-18', type_note: 'AS350B3',
  });
  const lasd = aircraft({
    id: 'ad4835', registration: 'N955LA', callsign: 'N955LA', distance_m: 3000,
    operator: 'LASD Aero Bureau', operator_short: 'LASD', role: 'police', role_rule: 'catalog_hex',
    role_source: 'Inferred from base', role_checked: null,
  });
  const cessna = aircraft({
    id: 'a00004', registration: 'N4Q', callsign: 'N4Q', class: 'aircraft', category: 'A1', distance_m: 4000,
    description: 'CESSNA 172', role: 'private', role_rule: 'registry_owner_type', role_source: 'FAA registry',
  });

  it('tags the list and labels, and explains the identification in the detail panel', async () => {
    render(App, { fetchFn: server([snapshot({ aircraft: [lapd, lasd, cessna] })]), pollMs: POLL });
    await advance(0);
    const list = screen.getByTestId('aircraft-list');
    expect(within(list).getByText('LAPD')).toHaveClass('role-police');
    expect(within(list).getByText('Private')).toHaveClass('role-private');

    const labels = map().getSource('aircraft')!.setData.mock.calls.at(-1)![0].features.map(
      (f: { properties: { label: string } }) => f.properties.label,
    );
    expect(labels).toEqual(['N225LA\n015 LAPD', 'N955LA\n015 LASD', 'N4Q\n015']);

    await fireEvent.click(within(list).getByRole('button', { name: /N225LA/ }));
    expect(screen.getByTestId('operator')).toHaveTextContent('LAPD Air Support Division (Police)');
    expect(screen.getByTestId('detail')).toHaveTextContent('catalog, by ICAO address: FAA registry owner (confirmed 2026-10-18)');

    await fireEvent.click(within(list).getByRole('button', { name: /N955LA/ }));
    expect(screen.getByTestId('detail')).toHaveTextContent('(inferred, unconfirmed)');

    await fireEvent.click(within(list).getByRole('button', { name: /N4Q/ }));
    expect(screen.getByTestId('operator')).toHaveTextContent('Private owner (name withheld)');
    expect(screen.getByTestId('detail')).toHaveTextContent('FAA registry lists individuals as owners');
  });

  it('hover cards name the operator', async () => {
    render(App, { fetchFn: server([snapshot({ aircraft: [lapd, cessna] })]), pollMs: POLL });
    await advance(0);
    map().fire('mousemove:aircraft', { features: [{ properties: { id: lapd.id } }] });
    await tick();
    const card = screen.getByTestId('map-tooltip');
    expect(within(card).getByText('LAPD')).toHaveClass('role-police');
    expect(card).toHaveTextContent('LAPD Air Support Division · AEROSPATIALE AS-350 Ecureuil');

    map().fire('mousemove:aircraft', { features: [{ properties: { id: cessna.id } }] });
    await tick();
    expect(screen.getByTestId('map-tooltip')).toHaveTextContent('N4QPrivate');
    expect(screen.getByTestId('map-tooltip')).toHaveTextContent('CESSNA 172');
  });

  it('unidentified aircraft say so', async () => {
    render(App, { fetchFn: server([snapshot({ aircraft: [heli] })]), pollMs: POLL });
    await advance(0);
    await fireEvent.click(screen.getByRole('button', { name: /N225LA/ }));
    expect(screen.getByTestId('operator')).toHaveTextContent('Unknown (not in the catalog)');
  });
});

describe('Behaviors', () => {
  const circling = aircraft({
    behaviors: [{
      kind: 'circling', since: '2026-10-17T19:48:00.000+00:00', duration_s: 724, at_least: false,
      center: [-118.41, 34.01], radius_m: 161.4, turns: 12.3, altitude_baro_ft: 437,
    }],
  });
  const hovering = aircraft({
    id: 'ab4ea1', registration: 'N828AP', callsign: 'N828AP', distance_m: 4000,
    behaviors: [{
      kind: 'hovering', since: '2026-10-17T19:30:00.000+00:00', duration_s: 900, at_least: true,
      center: [-118.39, 34.02], radius_m: 29, turns: null, altitude_baro_ft: 1800,
    }],
  });

  it('shows chips, detail, hover lines and rings for current behaviors', async () => {
    render(App, { fetchFn: server([snapshot({ aircraft: [circling, hovering] })]), pollMs: POLL });
    await advance(0);
    const chips = screen.getAllByTestId('behavior-chip').map((c) => c.textContent);
    expect(chips).toEqual(['Circling 12 min', 'Hovering 15+ min']);

    const rings = map().getSource('behaviors')!.setData.mock.calls.at(-1)![0].features;
    expect(rings.map((f: { properties: { kind: string } }) => f.properties.kind)).toEqual(['circling', 'hovering']);
    expect(rings[0].geometry.coordinates).toHaveLength(65);

    await fireEvent.click(within(screen.getByTestId('aircraft-list')).getByRole('button', { name: /N225LA/ }));
    expect(screen.getByTestId('behavior')).toHaveTextContent(
      'Circling 12 min · 12 turns · radius 161 m · around 425 ft Inferred from the track, not the reason for it',
    );

    map().fire('mousemove:aircraft', { features: [{ properties: { id: 'ab4ea1' } }] });
    await tick();
    expect(screen.getByTestId('map-tooltip')).toHaveTextContent('Hovering 15+ min · radius 29 m · around 1,800 ft');
  });

  it('frames a current behavior once and removes the activity when it ends', async () => {
    render(App, {
      fetchFn: server([
        snapshot({ aircraft: [circling] }),
        snapshot({ sequence: 2, aircraft: [circling] }),
        snapshot({ sequence: 3, aircraft: [heli] }),
      ]),
      pollMs: POLL,
    });
    await advance(0);
    const activity = screen.getByTestId('activity');
    const m = map();
    const homeFits = m.fitBounds.mock.calls.length;
    await fireEvent.click(within(activity).getByRole('button', { name: /N225LA.*Circling 12 min/ }));

    const [[west, south], [east, north]] = m.fitBounds.mock.lastCall![0];
    const [longitude, latitude] = circling.behaviors[0].center;
    expect((west + east) / 2).toBeCloseTo(longitude, 4);
    expect((south + north) / 2).toBeCloseTo(latitude, 4);
    expect(m.fitBounds).toHaveBeenCalledTimes(homeFits + 1);
    expect(screen.getByTestId('detail')).toHaveTextContent('N225LA');

    await advance(2 * POLL);
    expect(m.fitBounds).toHaveBeenCalledTimes(homeFits + 1);
    expect(screen.queryByTestId('activity')).toBeNull();
  });

  describe('home view, outlines and weather', () => {
    const weather: Weather = {
      status: 'ok',
      source: { name: 'National Weather Service', url: 'https://www.weather.gov' },
      station: { id: 'KSMO', name: 'Santa Monica, Santa Monica Municipal Airport', distance_m: 1600 },
      observation: {
        observed_at: '2026-10-17T19:08:00.000+00:00', description: 'Partly Cloudy', temperature_f: 64,
        dewpoint_f: 54, humidity_pct: 68, wind_direction_deg: 230, wind_speed_mph: 15, wind_gust_mph: 21,
        visibility_mi: 10, ceiling_ft: null, clouds: [{ amount: 'FEW', base_ft: 2700 }],
      },
      age_s: 3120,
      checked_at: '2026-10-17T20:00:00.000+00:00',
      error: null,
    };
    const city: Outlines = {
      type: 'FeatureCollection',
      features: [{
        type: 'Feature', properties: { name: 'LA city limits' },
        geometry: { type: 'LineString', coordinates: [[-118.5, 34.0], [-118.3, 34.1]] },
      }],
    };

    function latSpan() {
      const [[, south], [, north]] = map().fitBounds.mock.calls.at(-1)![0];
      return north - south;
    }

    it('frames the view radius when set, else the visible area', async () => {
      render(App, { fetchFn: server([snapshot()]), pollMs: 60_000 });
      await advance(0);
      const visible = latSpan();
      cleanup();
      const base = snapshot();
      render(App, { fetchFn: server([snapshot({ area: { ...base.area, view_radius_m: 3218.7 } })]), pollMs: 60_000 });
      await advance(0);
      expect(latSpan()).toBeLessThan(visible * 0.5);
    });

    it('draws outlines and names them in the legend', async () => {
      render(App, { fetchFn: server([snapshot()], city), pollMs: 60_000 });
      await advance(0);
      expect(map().layers.indexOf('outlines')).toBeLessThan(map().layers.indexOf('rings'));
      expect(map().getSource('outlines')!.setData.mock.calls.at(-1)![0].features).toHaveLength(1);
      expect(screen.getByTestId('legend')).toHaveTextContent('LA city limits');
    });

    it('summarizes the weather in the sidebar with observation details on demand', async () => {
      render(App, { fetchFn: server([snapshot({ weather })]), pollMs: 60_000 });
      await advance(0);
      const card = screen.getByTestId('weather') as HTMLDetailsElement;
      const summary = card.querySelector('summary')!;
      expect(card.closest('aside')).toBe(screen.getByRole('complementary'));
      expect(card.open).toBe(false);
      expect(summary).toHaveTextContent('64° Partly Cloudy · Wind SW 15 mph');
      expect(summary).not.toHaveTextContent('Weather');
      await fireEvent.click(summary);
      expect(card.open).toBe(true);
      expect(card).toHaveTextContent('NWS · KSMO');
      expect(card).toHaveTextContent('64°');
      expect(card).toHaveTextContent('Partly Cloudy');
      expect(card).toHaveTextContent('Wind SW 15 mph, gusts 21');
      expect(card).toHaveTextContent('Visibility 10 mi, no ceiling');
      expect(card).toHaveTextContent('52 min ago');
      expect(card).not.toHaveClass('stale');

      await advance(70 * 60_000);
      expect(card).toHaveClass('stale');
      expect(summary).toHaveTextContent('Stale');
      expect(card).toHaveTextContent('station not reporting');

      await fireEvent.click(screen.getByTestId('compact-toggle'));
      const compactWeather = screen.getByTestId('weather');
      expect(compactWeather).toHaveClass('compact');
      expect(compactWeather).not.toHaveAttribute('open');
    });

    it('applies the text scale from the config', async () => {
      const base = snapshot();
      render(App, { fetchFn: server([snapshot({ area: { ...base.area, text_scale: 1.5 } })]), pollMs: 60_000 });
      await advance(0);
      expect(document.documentElement.style.getPropertyValue('--ui-scale')).toBe('1.5');
    });

    it('keeps TV weather, legend and aircraft visible while removing controls', async () => {
      localStorage.setItem('airspace:compact', '1');
      const base = snapshot();
      const area = {
        ...base.area,
        tv_display: true,
        show_filters: true,
        filters: { ...base.area.filters!, max_altitude_baro_ft: 2000 },
      };
      const updated = { ...heli, altitude_baro_ft: 1900 };
      const fetchFn = server([
        snapshot({ area, aircraft: [heli, jet], weather }),
        snapshot({ area, aircraft: [updated, jet], weather }),
      ], city);
      render(App, { fetchFn, pollMs: POLL });
      await advance(0);

      expect(screen.queryAllByRole('button')).toEqual([]);
      expect(screen.queryByTestId('altitude-filter')).toBeNull();
      expect(screen.getByTestId('feed-status')).toHaveTextContent('Live');
      const aside = within(screen.getByRole('complementary'));
      const legend = screen.getByTestId('legend');
      const card = aside.getByTestId('weather');
      expect(legend).toHaveTextContent('LA city limits');
      expect(legend.querySelector('h2')).toBeNull();
      expect(card).toHaveTextContent('Wind SW 15 mph, gusts 21');
      expect(card).toHaveTextContent('Visibility 10 mi, no ceiling');
      expect(card.querySelector('.temp')).toHaveTextContent('64°');
      expect(card.querySelector('.clock')).toHaveTextContent(/PT$/);
      expect(legend.querySelector('summary')).toBeNull();
      expect(card.querySelector('summary')).toBeNull();
      expect(card).not.toHaveTextContent(/^Weather/);
      const list = aside.getByTestId('aircraft-list');
      expect(list).toHaveTextContent('N225LA');
      expect(list).not.toHaveTextContent('UAL2113');
      expect(instances).toHaveLength(1);
      const m = map();
      expect(m.options.interactive).toBe(false);
      expect(m.handlers.has('click')).toBe(false);
      expect(m.addControl).toHaveBeenCalledExactlyOnceWith(expect.any(AttributionControl));
      expect(m.fitBounds).toHaveBeenCalled();

      await advance(POLL);
      expect(list).toHaveTextContent('1,900 ft');
      expect(instances).toHaveLength(1);
    });

    it('hides the filter controls and applies the configured filters', async () => {
      const base = snapshot();
      const parked = aircraft({ id: 'a00001', registration: 'N1', callsign: 'PARKED', on_ground: true, distance_m: 900 });
      const area = {
        ...base.area,
        show_filters: false,
        filters: { ...base.area.filters!, on_ground: true, max_altitude_baro_ft: 2000 },
      };
      render(App, { fetchFn: server([snapshot({ area, aircraft: [heli, jet, parked] })]), pollMs: 60_000 });
      await advance(0);
      expect(screen.queryByTestId('altitude-filter')).toBeNull();
      const rows = within(screen.getByTestId('aircraft-list')).getAllByRole('button');
      // The jet at 2,550 ft is above the configured 2,000 ft; the parked aircraft is shown
      expect(rows.map((r) => r.dataset.id)).toEqual(['a00001', 'a1f48f']);
    });

    it('adds a configured altitude that is not a preset to the menu', async () => {
      const base = snapshot();
      const area = { ...base.area, filters: { ...base.area.filters!, max_altitude_baro_ft: 3000 } };
      render(App, { fetchFn: server([snapshot({ area })]), pollMs: 60_000 });
      await advance(0);
      const select = screen.getByTestId('altitude-filter') as HTMLSelectElement;
      expect(select.selectedOptions[0].textContent).toBe('Below 3,000 ft (barometric)');
    });

    it('leaves the card out when weather is off', async () => {
      render(App, { fetchFn: server([snapshot({ weather: null })]), pollMs: 60_000 });
      await advance(0);
      expect(screen.queryByTestId('weather')).toBeNull();
    });
  });
});
