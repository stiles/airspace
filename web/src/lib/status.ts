import type { ApiError } from './api';
import { clock, seconds } from './format';
import type { Snapshot } from './types';

export type Level = 'ok' | 'warn' | 'alert' | 'info';

export interface StatusView {
  level: Level;
  label: string;
  detail: string;
  /** The map and list may not reflect current traffic */
  untrusted: boolean;
}

/**
 * One status for the header. Any failure, local or upstream, reads as
 * "not current" and never as an empty sky.
 */
export function statusView(snapshot: Snapshot | null, error: ApiError | null, elapsedS: number): StatusView {
  const view = liveView(snapshot, error, elapsedS);
  if (snapshot?.mode === 'replay' && snapshot.replay && !error) {
    // A recorded outage must read as an outage too
    return {
      ...view,
      level: view.level === 'ok' ? 'info' : view.level,
      label: `Replay: ${view.label.toLowerCase()}`,
      detail: `Recorded state at ${clock(snapshot.replay.at)}. ${view.detail}`,
    };
  }
  return view;
}

function liveView(snapshot: Snapshot | null, error: ApiError | null, elapsedS: number): StatusView {
  if (!snapshot) {
    return error
      ? { level: 'alert', label: 'No connection', detail: `${error.message}. Is \`airspace serve\` running?`, untrusted: true }
      : { level: 'info', label: 'Connecting', detail: 'Waiting for the local server', untrusted: true };
  }

  const feed = snapshot.feed;
  const age = feed.last_success_age_s === null ? null : feed.last_success_age_s + elapsedS;
  const updated = age === null ? 'no successful update yet' : `updated ${seconds(age)} ago`;

  if (error) {
    return {
      level: 'alert',
      label: 'Local server not responding',
      detail: `${error.message}. Positions are aging out; last data ${clock(feed.last_success_at)}.`,
      untrusted: true,
    };
  }
  switch (feed.status) {
    case 'paused':
      return {
        level: 'alert',
        label: 'Collection paused',
        detail: `${feed.paused ?? 'Stopped'}. Fix the cause, then restart \`airspace serve\`.`,
        untrusted: true,
      };
    case 'stale':
      return {
        level: 'alert',
        label: 'No current data',
        detail: `Feed ${updated}${feed.last_error ? ` (${feed.last_error})` : ''}. An empty map here does not mean an empty sky.`,
        untrusted: true,
      };
    case 'degraded':
      return {
        level: 'warn',
        label: 'Retrying',
        detail: `Last poll failed${feed.last_error ? `: ${feed.last_error}` : ''}; ${updated}`,
        untrusted: false,
      };
    case 'delayed':
      return { level: 'warn', label: 'Delayed', detail: `Feed ${updated}`, untrusted: false };
    case 'starting':
      return { level: 'info', label: 'Starting', detail: 'Waiting for the first poll', untrusted: true };
    default:
      return { level: 'ok', label: 'Live', detail: `Feed ${updated}`, untrusted: false };
  }
}

export function emptyListMessage(view: StatusView, filtered: boolean): string {
  if (view.untrusted) return 'No current positions. See the feed status above.';
  return filtered ? 'No aircraft match the current filters.' : 'No aircraft reported in the area right now.';
}
