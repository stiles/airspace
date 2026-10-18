// GeoJSON for the map. Coordinates are always [longitude, latitude].

import type { Feature, FeatureCollection, LineString, MultiLineString, Point } from 'geojson';
import { identity, METERS_PER_MILE } from './format';
import type { Aircraft, LonLat } from './types';

const EARTH_RADIUS_M = 6_371_008.8;

/** Point at a bearing and distance on a sphere; accurate to well under a pixel at these ranges. */
export function destination([lon, lat]: LonLat, bearingDeg: number, meters: number): LonLat {
  const rad = Math.PI / 180;
  const d = meters / EARTH_RADIUS_M;
  const b = bearingDeg * rad;
  const φ1 = lat * rad;
  const λ1 = lon * rad;
  const φ2 = Math.asin(Math.sin(φ1) * Math.cos(d) + Math.cos(φ1) * Math.sin(d) * Math.cos(b));
  const λ2 = λ1 + Math.atan2(Math.sin(b) * Math.sin(d) * Math.cos(φ1), Math.cos(d) - Math.sin(φ1) * Math.sin(φ2));
  return [((λ2 / rad + 540) % 360) - 180, φ2 / rad];
}

export function circle(center: LonLat, meters: number, steps = 128): LonLat[] {
  const ring: LonLat[] = [];
  for (let i = 0; i < steps; i++) ring.push(destination(center, (360 * i) / steps, meters));
  ring.push(ring[0]);
  return ring;
}

export type RingKind = 'ring' | 'visible' | 'collection' | 'home';

/** Mile rings inside the visible radius, the visible boundary and the collection boundary. */
export function rings(
  home: LonLat,
  visibleM: number,
  collectionM: number,
): FeatureCollection<LineString | Point, { kind: RingKind; label: string }> {
  const features: Feature<LineString | Point, { kind: RingKind; label: string }>[] = [];
  const add = (meters: number, kind: RingKind, label: string) => {
    features.push({ type: 'Feature', geometry: { type: 'LineString', coordinates: circle(home, meters) }, properties: { kind, label } });
    features.push({ type: 'Feature', geometry: { type: 'Point', coordinates: destination(home, 0, meters) }, properties: { kind, label } });
  };
  const visibleMiles = visibleM / METERS_PER_MILE;
  for (let mi = 1; mi < visibleMiles - 0.25; mi++) add(mi * METERS_PER_MILE, 'ring', `${mi} mi`);
  add(visibleM, 'visible', `${+visibleMiles.toFixed(1)} mi`);
  if (collectionM > visibleM * 1.05) add(collectionM, 'collection', 'Collection limit');
  features.push({ type: 'Feature', geometry: { type: 'Point', coordinates: home }, properties: { kind: 'home', label: '' } });
  return { type: 'FeatureCollection', features };
}

export type IconName = 'plane' | 'plane-still' | 'heli' | 'heli-still' | 'surface';

export function iconFor(a: Pick<Aircraft, 'class' | 'track_deg'>): IconName {
  if (a.class === 'surface') return 'surface';
  const helicopter = a.class === 'rotorcraft' || a.class === 'gyroplane';
  // No track: an unrotated symbol, never a guess from heading
  if (a.track_deg === null) return helicopter ? 'heli-still' : 'plane-still';
  return helicopter ? 'heli' : 'plane';
}

export interface AircraftProps {
  id: string;
  icon: IconName;
  rotation: number;
  kind: Aircraft['class'];
  freshness: Aircraft['freshness'];
  selected: boolean;
  label: string;
}

export function aircraftCollection(aircraft: Aircraft[], selectedId: string | null): FeatureCollection<Point, AircraftProps> {
  return {
    type: 'FeatureCollection',
    features: aircraft.map((a) => {
      const alt = a.on_ground ? 'GND' : a.altitude_baro_ft === null ? '' : String(Math.round(a.altitude_baro_ft / 100)).padStart(3, '0');
      return {
        type: 'Feature',
        id: a.id,
        geometry: { type: 'Point', coordinates: [a.longitude, a.latitude] },
        properties: {
          id: a.id,
          icon: iconFor(a),
          rotation: a.track_deg ?? 0,
          kind: a.class,
          freshness: a.freshness,
          selected: a.id === selectedId,
          label: [identity(a), [alt, a.role && a.role !== 'private' ? a.operator_short : null].filter(Boolean).join(' ')]
            .filter(Boolean)
            .join('\n'),
        },
      };
    }),
  };
}

export interface TrailProps {
  id: string;
  kind: Aircraft['class'];
  freshness: Aircraft['freshness'];
  selected: boolean;
}

export function trailCollection(aircraft: Aircraft[], selectedId: string | null): FeatureCollection<MultiLineString, TrailProps> {
  return {
    type: 'FeatureCollection',
    features: aircraft
      .filter((a) => a.trail.length > 0)
      .map((a) => ({
        type: 'Feature',
        id: a.id,
        // Separate segments stay separate: a coverage gap is never drawn as a route
        geometry: { type: 'MultiLineString', coordinates: a.trail },
        properties: { id: a.id, kind: a.class, freshness: a.freshness, selected: a.id === selectedId },
      })),
  };
}
