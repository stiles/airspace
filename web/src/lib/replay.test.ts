import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { snapshot } from '../test/fixtures';
import { exportUrl } from './api';
import { ReplayController } from './replay.svelte';
import { bins, clampTime, stepTime, toTime, toX } from './timeline';
import type { Timeline } from './types';

const T0 = Date.parse('2026-10-17T19:00:00Z');
const timeline = (overrides: Partial<Timeline> = {}): Timeline => ({
  session_id: 1,
  start: T0,
  end: T0 + 60_000,
  polls: {
    t: [0, 10, 20, 30, 40, 50, 60].map((s) => T0 + s * 1000),
    ok: [true, true, false, true, true, true, true],
    aircraft: [5, 5, 0, 6, 6, 6, 6],
    rotorcraft: [0, 0, 0, 1, 1, 0, 0],
  },
  gaps: [],
  ...overrides,
});

describe('timeline helpers', () => {
  const tl = timeline();
  it('clamps and steps between polls', () => {
    expect(clampTime(T0 - 5000, tl)).toBe(T0);
    expect(clampTime(T0 + 99_000, tl)).toBe(T0 + 60_000);
    expect(stepTime(T0 + 15_000, tl, 1)).toBe(T0 + 20_000);
    expect(stepTime(T0 + 20_000, tl, 1)).toBe(T0 + 30_000);
    expect(stepTime(T0 + 20_000, tl, -1)).toBe(T0 + 10_000);
    expect(stepTime(T0, tl, -1)).toBe(T0);
    expect(stepTime(T0 + 60_000, tl, 1)).toBe(T0 + 60_000);
  });
  it('maps time to pixels and back', () => {
    expect(toX(T0 + 30_000, T0, T0 + 60_000, 600)).toBe(300);
    expect(toTime(300, T0, T0 + 60_000, 600)).toBe(T0 + 30_000);
    expect(toTime(-50, T0, T0 + 60_000, 600)).toBe(T0);
  });
  it('bins mark failures and helicopter presence', () => {
    const b = bins(tl, 6, 1);
    expect(b).toHaveLength(6);
    expect(b.filter((x) => x.failed).length).toBe(1);
    expect(b.filter((x) => x.rotorcraft).length).toBe(2);
    expect(b.reduce((n, x) => n + x.polls, 0)).toBe(7);
  });
  it('builds export URLs', () => {
    expect(exportUrl(3)).toBe('/api/sessions/3/export?format=geojson&geometry=tracks');
    expect(exportUrl(3, { format: 'json' })).toBe('/api/sessions/3/export?format=json');
    expect(exportUrl(3, { geometry: 'points', aircraft: 'a1f48f' })).toBe(
      '/api/sessions/3/export?format=geojson&geometry=points&aircraft=a1f48f',
    );
  });
});

describe('ReplayController', () => {
  const sessions = [
    { id: 1, provider: 'adsbfi', mode: 'collect', started_at: '', ended_at: null, end_reason: null, polls: 7, first_poll_at: null, last_poll_at: null },
    { id: 2, provider: 'adsbfi', mode: 'serve', started_at: '', ended_at: null, end_reason: null, polls: 7, first_poll_at: null, last_poll_at: null },
  ];
  let replayAts: string[];
  let delayMs: number;

  function fakeFetch() {
    return vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input), 'http://localhost');
      let body: unknown;
      if (url.pathname === '/api/sessions') body = sessions;
      else if (url.pathname.endsWith('/timeline')) body = timeline({ session_id: Number(url.pathname.split('/')[3]) });
      else if (url.pathname === '/api/replay') {
        const at = url.searchParams.get('at')!;
        replayAts.push(at);
        if (delayMs) await new Promise((r) => setTimeout(r, delayMs));
        body = snapshot({ mode: 'replay', generated_at: at, sequence: replayAts.length });
      } else return new Response('{}', { status: 404 });
      return new Response(JSON.stringify(body), { status: 200 });
    }) as unknown as typeof fetch;
  }

  beforeEach(() => {
    replayAts = [];
    delayMs = 0;
    vi.useFakeTimers();
  });
  afterEach(() => vi.useRealTimers());

  it('opens the latest session at its end', async () => {
    const locations: string[] = [];
    const c = new ReplayController({ fetchFn: fakeFetch(), onLocation: (s, at) => locations.push(`${s}@${at}`) });
    await c.open();
    await vi.advanceTimersByTimeAsync(0);
    expect(c.sessionId).toBe(2);
    expect(c.at).toBe(T0 + 60_000);
    expect(replayAts).toEqual(['2026-10-17T19:01:00.000Z']);
    expect(c.snapshot?.mode).toBe('replay');
    expect(locations).toEqual(['2@2026-10-17T19:01:00.000Z']);
  });

  it('plays at speed, stops at the end and records the location on pause', async () => {
    const locations: string[] = [];
    const c = new ReplayController({ fetchFn: fakeFetch(), tickMs: 250, onLocation: (s, at) => locations.push(at) });
    await c.open(1, T0);
    c.speed = 16;
    c.play();
    await vi.advanceTimersByTimeAsync(1000); // 1 s of wall time = 16 s of replay
    expect(c.at).toBe(T0 + 16_000);
    expect(c.playing).toBe(true);
    await vi.advanceTimersByTimeAsync(5000);
    expect(c.playing).toBe(false);
    expect(c.at).toBe(T0 + 60_000);
    expect(locations.at(-1)).toBe('2026-10-17T19:01:00.000Z');
    // Play from the end restarts at the beginning
    c.play();
    expect(c.at).toBe(T0);
    c.pause();
  });

  it('keeps one request in flight and always catches up to the latest playhead', async () => {
    delayMs = 700; // slower than the tick
    const c = new ReplayController({ fetchFn: fakeFetch(), tickMs: 100 });
    await c.open(1, T0);
    c.speed = 4;
    c.play();
    await vi.advanceTimersByTimeAsync(3000);
    c.pause();
    await vi.advanceTimersByTimeAsync(3000);
    // 30 ticks happened, but requests are bounded by the response time, and the last one is the final playhead
    expect(replayAts.length).toBeLessThan(10);
    expect(replayAts.at(-1)).toBe(new Date(c.at).toISOString());
    expect(c.snapshot?.generated_at).toBe(new Date(c.at).toISOString());
  });

  it('steps between polls and pauses', async () => {
    const c = new ReplayController({ fetchFn: fakeFetch() });
    await c.open(1, T0 + 15_000);
    c.play();
    c.step(1);
    expect(c.playing).toBe(false);
    expect(c.at).toBe(T0 + 20_000);
    c.step(-1);
    expect(c.at).toBe(T0 + 10_000);
  });

  it('reports a missing session list as an error', async () => {
    const down = vi.fn(async () => {
      throw new TypeError('fetch failed');
    }) as unknown as typeof fetch;
    const c = new ReplayController({ fetchFn: down });
    await c.open();
    expect(c.error?.message).toContain("Can't reach");
  });
});
