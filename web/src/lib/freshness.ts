import type { Aircraft, Freshness, Snapshot } from './types';

/**
 * Age aircraft by the time elapsed since the snapshot was fetched, using the
 * server's thresholds. Keeps markers honest when the local server stops
 * responding: they fade, then disappear, rather than freezing in place.
 */
export function ageAircraft(aircraft: Aircraft[], elapsedS: number, thresholds: Snapshot['thresholds']): Aircraft[] {
  const out: Aircraft[] = [];
  for (const a of aircraft) {
    const age = a.position_age_s === null ? null : a.position_age_s + elapsedS;
    let freshness: Freshness;
    if (age === null) {
      freshness = a.freshness === 'unknown' && elapsedS <= thresholds.hide_s ? 'unknown' : 'hidden';
    } else if (age > thresholds.hide_s) {
      freshness = 'hidden';
    } else if (age > thresholds.fade_s) {
      freshness = 'fading';
    } else {
      freshness = 'fresh';
    }
    if (freshness !== 'hidden') out.push({ ...a, position_age_s: age, freshness });
  }
  return out;
}
