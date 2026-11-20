// Weather card text. Missing values drop their line rather than reading as zero.

import { cardinal } from './format';
import type { Observation } from './types';

/** Stations report about hourly; past two hours the reading is old news */
export const WEATHER_STALE_S = 2 * 3600;

const number = new Intl.NumberFormat('en-US');

export function temperature(obs: Observation): string | null {
  return obs.temperature_f === null ? null : `${Math.round(obs.temperature_f)}°`;
}

export function wind(obs: Observation): string | null {
  const speed = obs.wind_speed_mph;
  if (speed === null) return null;
  if (speed < 1) return 'Wind calm';
  const from = obs.wind_direction_deg === null ? 'variable' : cardinal(obs.wind_direction_deg);
  const gust = obs.wind_gust_mph !== null && obs.wind_gust_mph > speed ? `, gusts ${Math.round(obs.wind_gust_mph)}` : '';
  return `Wind ${from} ${Math.round(speed)} mph${gust}`;
}

export function sky(obs: Observation): string | null {
  const parts: string[] = [];
  if (obs.visibility_mi !== null) parts.push(`Visibility ${+obs.visibility_mi.toFixed(obs.visibility_mi < 3 ? 1 : 0)} mi`);
  if (obs.ceiling_ft !== null) parts.push(`ceiling ${number.format(obs.ceiling_ft)} ft`);
  else if (obs.clouds !== null) parts.push('no ceiling');
  if (!parts.length) return null;
  parts[0] = parts[0][0].toUpperCase() + parts[0].slice(1);
  return parts.join(', ');
}

export function observedAgo(ageS: number): string {
  if (ageS < 90) return 'just now';
  const m = Math.round(ageS / 60);
  if (m < 60) return `${m} min ago`;
  return `${Math.floor(m / 60)} hr ${m % 60} min ago`;
}

export function observedClock(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
}
