import { cleanup, fireEvent, render, screen, within } from '@testing-library/svelte';
import { tick } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Snapshot } from './lib/types';
import { aircraft, snapshot } from './test/fixtures';
import { instances } from './test/mapbox-mock';

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

/** Fake fetch that serves one step per request, repeating the last. */
function server(steps: Step[]) {
  let i = 0;
  const fetchFn = vi.fn(async () => {
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

    await fireEvent.click(rows[0]);
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

  it('creates map layers once and only replaces data on updates', async () => {
    const fetchFn = server([snapshot({ aircraft: [heli, jet] })]);
    render(App, { fetchFn, pollMs: POLL });
    await advance(0);
    const m = map();
    const layers = m.addLayerCalls;
    const sources = m.addSourceCalls;
    expect(m.layers).toEqual(expect.arrayContaining(['rings', 'trails', 'aircraft', 'selection']));

    for (let i = 0; i < 5; i++) await advance(POLL);
    expect(m.addLayerCalls).toBe(layers);
    expect(m.addSourceCalls).toBe(sources);
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
