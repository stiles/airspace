import type { Timeline } from './types';

export function clampTime(t: number, tl: Timeline): number {
  if (tl.start === null || tl.end === null) return t;
  return Math.min(tl.end, Math.max(tl.start, t));
}

/** The next (dir 1) or previous (dir -1) poll time, strictly after or before `t`. */
export function stepTime(t: number, tl: Timeline, dir: 1 | -1): number {
  const times = tl.polls.t;
  if (!times.length) return t;
  if (dir > 0) return times.find((x) => x > t) ?? times[times.length - 1];
  for (let i = times.length - 1; i >= 0; i--) if (times[i] < t) return times[i];
  return times[0];
}

export function toX(t: number, start: number, end: number, width: number): number {
  return end > start ? ((t - start) / (end - start)) * width : 0;
}

export function toTime(x: number, start: number, end: number, width: number): number {
  return width > 0 ? start + (Math.min(width, Math.max(0, x)) / width) * (end - start) : start;
}

export interface Bin {
  x: number;
  width: number;
  polls: number;
  failed: number;
  rotorcraft: boolean;
}

/** Collapse polls into pixel-width bins; a bin with any failed poll is marked. */
export function bins(tl: Timeline, width: number, binPx = 2): Bin[] {
  const { start, end } = tl;
  if (start === null || end === null || width <= 0) return [];
  const count = Math.max(1, Math.floor(width / binPx));
  const out: Bin[] = Array.from({ length: count }, (_, i) => ({
    x: (i * width) / count,
    width: width / count,
    polls: 0,
    failed: 0,
    rotorcraft: false,
  }));
  tl.polls.t.forEach((t, i) => {
    const index = Math.min(count - 1, Math.floor((toX(t, start, end, width) / width) * count));
    const bin = out[index];
    bin.polls++;
    if (!tl.polls.ok[i]) bin.failed++;
    if (tl.polls.rotorcraft[i] > 0) bin.rotorcraft = true;
  });
  return out;
}
