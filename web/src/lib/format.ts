// Display formatting. Unknown stays unknown; zero is a real value.

import type { Aircraft, AircraftClass, Behavior, Role } from './types';

export const METERS_PER_MILE = 1609.344;
export const UNKNOWN = 'Unknown';

const number = new Intl.NumberFormat('en-US');

export function feet(value: number | null): string {
  return value === null ? UNKNOWN : `${number.format(value)} ft`;
}

export function altitude(a: Pick<Aircraft, 'altitude_baro_ft' | 'on_ground'>): string {
  if (a.on_ground === true) return 'On ground';
  return feet(a.altitude_baro_ft);
}

export function knots(value: number | null): string {
  return value === null ? UNKNOWN : `${Math.round(value)} kt`;
}

export function degrees(value: number | null): string {
  return value === null ? UNKNOWN : `${Math.round(value) % 360}°`;
}

export function verticalRate(value: number | null): string {
  if (value === null) return UNKNOWN;
  if (value === 0) return 'Level (0 ft/min)';
  return `${value > 0 ? 'Climbing' : 'Descending'} ${number.format(Math.abs(value))} ft/min`;
}

export function miles(meters: number): string {
  const mi = meters / METERS_PER_MILE;
  return `${mi < 10 ? mi.toFixed(1) : Math.round(mi)} mi`;
}

const CARDINALS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];

export function cardinal(bearing: number): string {
  return CARDINALS[Math.round((((bearing % 360) + 360) % 360) / 45) % 8];
}

export function seconds(value: number | null): string {
  if (value === null) return UNKNOWN;
  if (value < 0) return 'In the future';
  if (value < 60) return `${Math.round(value)}s`;
  const m = Math.floor(value / 60);
  if (m < 60) return `${m} min ${Math.round(value % 60)}s`;
  return `${Math.floor(m / 60)} hr ${m % 60} min`;
}

export function clock(iso: string | null): string {
  if (!iso) return UNKNOWN;
  return new Date(iso).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', second: '2-digit' });
}

export function identity(a: Pick<Aircraft, 'callsign' | 'registration' | 'id'>): string {
  return a.callsign ?? a.registration ?? a.id.toUpperCase();
}

const CLASS_LABELS: Record<AircraftClass, string> = {
  rotorcraft: 'Helicopter',
  gyroplane: 'Gyroplane',
  surface: 'Surface vehicle',
  aircraft: 'Aircraft',
  unknown: 'Unknown type',
};

const RULE_LABELS: Record<string, string> = {
  adsb_category: 'ADS-B emitter category',
  faa_registry: 'FAA registry',
  none: 'no documented source',
};

export function classLabel(kind: AircraftClass): string {
  return CLASS_LABELS[kind] ?? kind;
}

export function classSource(rule: string): string {
  return RULE_LABELS[rule] ?? rule;
}

const SOURCE_LABELS: Record<string, string> = {
  adsb_icao: 'ADS-B',
  adsb_icao_nt: 'ADS-B (non-transponder)',
  adsr_icao: 'ADS-R (rebroadcast)',
  tisb_icao: 'TIS-B (radar)',
  tisb_other: 'TIS-B (radar)',
  tisb_trackfile: 'TIS-B track file (radar)',
  mlat: 'MLAT',
  mode_s: 'Mode S',
  adsc: 'ADS-C',
};

export function positionSource(value: string | null): string {
  return value === null ? UNKNOWN : (SOURCE_LABELS[value] ?? value);
}

const ROLE_LABELS: Record<Role, string> = {
  police: 'Police',
  fire: 'Fire',
  medical: 'Medical',
  news: 'News',
  military: 'Military',
  tour: 'Tour',
  utility: 'Utility',
  training: 'Training',
  private: 'Private',
};

export function roleLabel(role: Role): string {
  return ROLE_LABELS[role] ?? role;
}

/** Short tag for lists and labels: the operator's short name, else its role. */
export function operatorTag(a: Pick<Aircraft, 'operator_short' | 'operator' | 'role'>): string | null {
  if (!a.role) return null;
  return a.operator_short ?? (a.role === 'private' ? 'Private' : roleLabel(a.role));
}

const ROLE_RULE_LABELS: Record<string, string> = {
  catalog_hex: 'catalog, by ICAO address',
  catalog_registration: 'catalog, by registration',
  catalog_callsign: 'catalog, by callsign',
  owner_pattern: "registered owner's name",
  registry_owner_type: 'FAA registry lists individuals as owners',
};

export function roleRule(rule: string): string {
  return ROLE_RULE_LABELS[rule] ?? rule;
}

function minutes(b: Behavior): string {
  const m = Math.max(1, Math.round(b.duration_s / 60));
  return `${m}${b.at_least ? '+' : ''} min`;
}

const BEHAVIOR_NAMES: Record<Behavior['kind'], string> = { circling: 'Circling', hovering: 'Hovering', low: 'Low' };

/** Short chip text, e.g. "Circling 12 min". */
export function behaviorChip(b: Behavior): string {
  return `${BEHAVIOR_NAMES[b.kind]} ${minutes(b)}`;
}

/** Fuller description for the detail panel and hover card. */
export function behaviorText(b: Behavior): string {
  const parts = [behaviorChip(b)];
  if (b.kind === 'circling' && b.turns !== null) parts.push(`${b.turns.toFixed(b.turns < 10 ? 1 : 0)} turns`);
  if (b.kind !== 'low') parts.push(`radius ${Math.round(b.radius_m)} m`);
  if (b.altitude_baro_ft !== null) parts.push(`around ${number.format(Math.round(b.altitude_baro_ft / 25) * 25)} ft`);
  return parts.join(' · ');
}
