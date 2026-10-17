import { ApiError, fetchSessions, fetchSnapshot, fetchTimeline } from './api';
import { clampTime, stepTime } from './timeline';
import type { SessionInfo, Snapshot, Timeline } from './types';

export const SPEEDS = [1, 4, 16, 60];

interface Options {
  fetchFn?: typeof fetch;
  tickMs?: number;
  /** Called with the session and time to show in the URL, when paused or seeking */
  onLocation?: (sessionId: number, atIso: string) => void;
}

/**
 * Replay state and playback. Playback advances a playhead; snapshots come from
 * /api/replay with at most one request in flight, always for the latest playhead.
 */
export class ReplayController {
  sessions = $state<SessionInfo[]>([]);
  timeline = $state<Timeline | null>(null);
  sessionId = $state<number | null>(null);
  at = $state(0);
  playing = $state(false);
  speed = $state(16);
  snapshot = $state<Snapshot | null>(null);
  error = $state<ApiError | null>(null);

  #fetchFn: typeof fetch;
  #tickMs: number;
  #onLocation?: Options['onLocation'];
  #timer: ReturnType<typeof setInterval> | undefined;
  #inFlight = false;
  #requested: number | null = null;
  #closed = false;

  constructor(options: Options = {}) {
    this.#fetchFn = options.fetchFn ?? ((...args) => fetch(...args));
    this.#tickMs = options.tickMs ?? 250;
    this.#onLocation = options.onLocation;
  }

  /** Load sessions and show `sessionId` (default: the most recent) at `at` (default: its end). */
  async open(sessionId?: number | null, at?: number | null): Promise<void> {
    try {
      this.sessions = await fetchSessions(this.#fetchFn);
    } catch (e) {
      this.error = e instanceof ApiError ? e : new ApiError(String(e));
      return;
    }
    const chosen = this.sessions.find((s) => s.id === sessionId) ?? this.sessions.at(-1);
    if (!chosen) {
      this.error = new ApiError('No recorded sessions yet');
      return;
    }
    await this.selectSession(chosen.id, at ?? null);
  }

  async selectSession(id: number, at: number | null = null): Promise<void> {
    this.pause();
    try {
      const timeline = await fetchTimeline(id, this.#fetchFn);
      if (timeline.start === null || timeline.end === null) throw new ApiError(`Session ${id} has no polls`);
      this.timeline = timeline;
      this.sessionId = id;
    } catch (e) {
      this.error = e instanceof ApiError ? e : new ApiError(String(e));
      return;
    }
    this.seek(at ?? this.timeline!.end!);
  }

  seek(t: number, { syncLocation = true } = {}): void {
    if (!this.timeline || this.sessionId === null) return;
    this.at = clampTime(t, this.timeline);
    this.#request();
    if (syncLocation) this.#location();
  }

  step(dir: 1 | -1): void {
    if (!this.timeline) return;
    this.pause();
    this.seek(stepTime(this.at, this.timeline, dir));
  }

  play(): void {
    const tl = this.timeline;
    if (!tl || this.playing) return;
    if (this.at >= tl.end!) this.seek(tl.start!, { syncLocation: false });
    this.playing = true;
    this.#timer = setInterval(() => this.tick(this.#tickMs), this.#tickMs);
  }

  pause(): void {
    if (!this.playing) return;
    this.playing = false;
    clearInterval(this.#timer);
    this.#location();
  }

  toggle(): void {
    if (this.playing) this.pause();
    else this.play();
  }

  /** Advance by `dtMs` of wall time at the current speed. */
  tick(dtMs: number): void {
    const tl = this.timeline;
    if (!tl) return;
    const next = this.at + dtMs * this.speed;
    if (next >= tl.end!) {
      this.seek(tl.end!, { syncLocation: false });
      this.pause();
      return;
    }
    this.seek(next, { syncLocation: false });
  }

  close(): void {
    this.#closed = true;
    this.playing = false;
    clearInterval(this.#timer);
  }

  #request(): void {
    if (this.#inFlight) return; // the playhead is re-read when the current request finishes
    const target = this.at;
    const sessionId = this.sessionId!;
    this.#inFlight = true;
    this.#requested = target;
    fetchSnapshot({ sessionId, at: new Date(target).toISOString() }, this.#fetchFn)
      .then((s) => {
        if (this.#closed || sessionId !== this.sessionId) return;
        this.snapshot = s;
        this.error = null;
      })
      .catch((e) => {
        if (!this.#closed) this.error = e instanceof ApiError ? e : new ApiError(String(e));
      })
      .finally(() => {
        this.#inFlight = false;
        if (!this.#closed && this.at !== this.#requested) this.#request();
      });
  }

  #location(): void {
    if (this.sessionId !== null) this.#onLocation?.(this.sessionId, new Date(this.at).toISOString());
  }
}
