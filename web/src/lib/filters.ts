import type { Aircraft, ConfigFilters } from './types';

export interface Filters {
  /** Maximum barometric altitude in feet; null shows every altitude */
  maxAltitudeFt: number | null;
  /** Airborne aircraft with no barometric altitude */
  includeUnknownAltitude: boolean;
  /** Aircraft reporting on the ground (parked and taxiing traffic at LAX) */
  includeOnGround: boolean;
  /** ADS-B surface vehicles and obstacles (categories C1-C5) */
  includeSurface: boolean;
  /** List only aircraft inside the visible radius; the map always shows the collection area */
  listVisibleOnly: boolean;
}

export const ALTITUDE_PRESETS: { label: string; value: number | null }[] = [
  { label: 'Any altitude', value: null },
  { label: 'Below 10,000 ft', value: 10_000 },
  { label: 'Below 5,000 ft', value: 5_000 },
  { label: 'Below 2,500 ft', value: 2_500 },
];

export function defaultFilters(maxAltitudeFt: number | null = null): Filters {
  return {
    maxAltitudeFt,
    includeUnknownAltitude: true,
    includeOnGround: false,
    includeSurface: false,
    listVisibleOnly: true,
  };
}

/** Starting filters from the config; the built-in defaults when the server sends none. */
export function fromConfig(c: ConfigFilters | undefined): Filters {
  if (!c) return defaultFilters();
  return {
    maxAltitudeFt: c.max_altitude_baro_ft,
    includeUnknownAltitude: c.unknown_altitude,
    includeOnGround: c.on_ground,
    includeSurface: c.surface_vehicles,
    listVisibleOnly: c.list_visible_only,
  };
}

/** The presets, plus a configured altitude that isn't one of them. */
export function altitudePresets(configured: number | null): { label: string; value: number | null }[] {
  if (configured === null || ALTITUDE_PRESETS.some((p) => p.value === configured)) return ALTITUDE_PRESETS;
  const custom = { label: `Below ${configured.toLocaleString('en-US')} ft`, value: configured };
  return [...ALTITUDE_PRESETS, custom].sort((a, b) => (b.value ?? Infinity) - (a.value ?? Infinity));
}

export function passes(a: Aircraft, f: Filters): boolean {
  if (a.class === 'surface') return f.includeSurface;
  if (a.on_ground === true) return f.includeOnGround;
  if (a.altitude_baro_ft === null) return f.includeUnknownAltitude;
  return f.maxAltitudeFt === null || a.altitude_baro_ft < f.maxAltitudeFt;
}

export function applyFilters(aircraft: Aircraft[], f: Filters): Aircraft[] {
  return aircraft.filter((a) => passes(a, f));
}
