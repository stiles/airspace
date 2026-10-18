// Hover card text for an aircraft on the map. Short lines; the detail panel has the rest.

import { altitude, cardinal, classLabel, identity, knots, miles, seconds } from './format';
import type { Aircraft } from './types';

export interface Tooltip {
  title: string;
  subtitle: string;
  lines: string[];
  note: string | null;
}

// Below this, call it level flight rather than a climb or descent
const TREND_FPM = 300;

export function tooltipFor(a: Aircraft): Tooltip {
  let alt = altitude(a);
  const vr = a.vertical_rate_fpm;
  if (!a.on_ground && vr !== null && Math.abs(vr) >= TREND_FPM) alt += vr > 0 ? ' ↑' : ' ↓';
  const speed = a.ground_speed_kt === null ? null : knots(a.ground_speed_kt);
  return {
    title: identity(a),
    subtitle: a.description ?? a.aircraft_type ?? classLabel(a.class),
    lines: [speed ? `${alt} · ${speed}` : alt, `${miles(a.distance_m)} ${cardinal(a.bearing_deg)} of home`],
    note:
      a.freshness === 'fading'
        ? `Position ${seconds(a.position_age_s)} old`
        : a.freshness === 'unknown'
          ? 'Position time unknown'
          : null,
  };
}
