import type { Aircraft } from './types';

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

export function passes(a: Aircraft, f: Filters): boolean {
  if (a.class === 'surface') return f.includeSurface;
  if (a.on_ground === true) return f.includeOnGround;
  if (a.altitude_baro_ft === null) return f.includeUnknownAltitude;
  return f.maxAltitudeFt === null || a.altitude_baro_ft < f.maxAltitudeFt;
}

export function applyFilters(aircraft: Aircraft[], f: Filters): Aircraft[] {
  return aircraft.filter((a) => passes(a, f));
}
