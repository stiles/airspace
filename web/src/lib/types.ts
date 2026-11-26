// Mirrors the snapshot produced by src/airspace/state.py (SNAPSHOT_VERSION 1)

import type { FeatureCollection, Geometry } from 'geojson';

export type Freshness = 'fresh' | 'fading' | 'unknown' | 'hidden';
export type FeedStatus = 'starting' | 'live' | 'delayed' | 'degraded' | 'stale' | 'paused';
export type AircraftClass = 'rotorcraft' | 'gyroplane' | 'surface' | 'aircraft' | 'unknown';
export type Role = 'police' | 'fire' | 'medical' | 'news' | 'military' | 'tour' | 'utility' | 'training' | 'private';

/** [longitude, latitude] */
export type LonLat = [number, number];

/** Inferred from the track (circling, hovering, low flight); describes it, not the reason */
export interface Behavior {
  kind: 'circling' | 'hovering' | 'low';
  since: string;
  duration_s: number;
  /** Began before the kept history, so it has lasted at least duration_s */
  at_least: boolean;
  center: LonLat;
  radius_m: number;
  turns: number | null;
  altitude_baro_ft: number | null;
}

export interface Aircraft {
  id: string;
  registration: string | null;
  callsign: string | null;
  aircraft_type: string | null;
  description: string | null;
  category: string | null;
  class: AircraftClass;
  class_rule: string;
  /** From the operator catalog; all null when the aircraft isn't identified */
  operator: string | null;
  operator_short: string | null;
  role: Role | null;
  role_rule: string | null;
  role_source: string | null;
  /** Date the identification was confirmed; null when inferred */
  role_checked: string | null;
  type_note: string | null;
  position_source: string | null;
  longitude: number;
  latitude: number;
  altitude_baro_ft: number | null;
  altitude_geom_ft: number | null;
  on_ground: boolean | null;
  ground_speed_kt: number | null;
  track_deg: number | null;
  vertical_rate_fpm: number | null;
  squawk: string | null;
  emergency: string | null;
  position_at: string | null;
  position_time_basis: string;
  time_flag: string | null;
  position_age_s: number | null;
  freshness: Freshness;
  last_received_at: string;
  in_latest_response: boolean;
  distance_m: number;
  bearing_deg: number;
  inside_visible: boolean;
  trail: LonLat[][];
  behaviors: Behavior[];
}

export interface LastSeen {
  id: string;
  registration: string | null;
  callsign: string | null;
  class: AircraftClass;
  operator_short: string | null;
  role: Role | null;
  longitude: number;
  latitude: number;
  position_at: string | null;
  last_received_at: string;
  inside_visible: boolean;
}

export interface AreaEvent {
  type: 'entered' | 'exited';
  aircraft_id: string;
  registration: string | null;
  callsign: string | null;
  at: string;
}

export interface Feed {
  status: FeedStatus;
  polls: number;
  successes: number;
  consecutive_failures: number;
  last_poll_at: string | null;
  last_success_at: string | null;
  last_success_age_s: number | null;
  last_error: string | null;
  last_error_at: string | null;
  paused: string | null;
}

export interface Snapshot {
  version: number;
  mode: 'live' | 'replay';
  generated_at: string;
  sequence: number;
  session_id?: number;
  source: { provider: string; name?: string; url?: string };
  area: {
    home: { latitude: number; longitude: number; label: string; title?: string };
    visible_radius_m: number;
    collection_radius_m: number;
    /** Radius the map frames; absent or null frames the visible area */
    view_radius_m?: number | null;
    /** Enlarges the interface around the map, e.g. 1.5 on a TV */
    text_scale?: number;
    /** Show the live dashboard as a read-only wall display */
    tv_display?: boolean;
    /** False hides the altitude menu and checkboxes; `filters` then stays fixed */
    show_filters?: boolean;
    /** IANA timezone for the TV wall clock, e.g. America/Los_Angeles */
    timezone?: string;
    filters?: ConfigFilters;
  };
  thresholds: { fade_s: number; hide_s: number; trail_gap_s: number; trail_duration_s: number };
  feed: Feed;
  aircraft: Aircraft[];
  last_seen: LastSeen[];
  events: AreaEvent[];
  replay?: { session_id: number; at: string; session_start: string | null; session_end: string | null };
  /** Live only; null when weather is turned off */
  weather?: Weather | null;
}

export interface Observation {
  observed_at: string;
  description: string | null;
  temperature_f: number | null;
  dewpoint_f: number | null;
  humidity_pct: number | null;
  wind_direction_deg: number | null;
  wind_speed_mph: number | null;
  wind_gust_mph: number | null;
  visibility_mi: number | null;
  /** Lowest broken, overcast or obscured layer */
  ceiling_ft: number | null;
  /** Null when the station reported no sky cover */
  clouds: { amount: string; base_ft: number | null }[] | null;
}

/** Boundaries from `display.outlines` in airspace.toml, e.g. city limits */
export type Outlines = FeatureCollection<Geometry, { name: string | null }>;

export interface Weather {
  status: 'starting' | 'ok' | 'stale' | 'unavailable';
  source: { name: string; url: string };
  station: { id: string; name: string | null; distance_m: number | null } | null;
  observation: Observation | null;
  age_s: number | null;
  checked_at: string | null;
  error: string | null;
}

/** Starting filters from `[filters]` in airspace.toml */
export interface ConfigFilters {
  max_altitude_baro_ft: number | null;
  unknown_altitude: boolean;
  on_ground: boolean;
  surface_vehicles: boolean;
  list_visible_only: boolean;
}

export const SUPPORTED_SNAPSHOT_VERSION = 1;

export interface SessionInfo {
  id: number;
  provider: string;
  mode: string;
  started_at: string;
  ended_at: string | null;
  end_reason: string | null;
  polls: number;
  first_poll_at: string | null;
  last_poll_at: string | null;
}

/** Per-poll outcomes for the replay scrubber; times are epoch milliseconds. */
export interface Timeline {
  session_id: number;
  start: number | null;
  end: number | null;
  polls: { t: number[]; ok: boolean[]; aircraft: number[]; rotorcraft: number[] };
  gaps: { kind: string; start: number; end: number }[];
}
