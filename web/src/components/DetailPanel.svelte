<script lang="ts">
  import {
    cardinal,
    classLabel,
    classSource,
    clock,
    degrees,
    feet,
    identity,
    knots,
    miles,
    positionSource,
    seconds,
    UNKNOWN,
    verticalRate,
  } from '../lib/format';
  import type { Aircraft, LastSeen } from '../lib/types';

  interface Props {
    aircraft: Aircraft | null;
    lastSeen: LastSeen | null;
    hiddenByFilters: boolean;
    exportHref?: string | null;
    onclose: () => void;
  }

  let { aircraft, lastSeen, hiddenByFilters, exportHref = null, onclose }: Props = $props();

  const FRESHNESS: Record<string, string> = {
    fresh: 'Current',
    fading: 'Aging',
    unknown: 'Position time unknown',
  };
</script>

{#if aircraft}
  {@const a = aircraft}
  <section class="detail" aria-label="Selected aircraft" data-testid="detail">
    <header>
      <div>
        <h2>{identity(a)}</h2>
        <p class="sub">{a.description ?? a.aircraft_type ?? 'Type unknown'}</p>
      </div>
      <button type="button" class="close" onclick={onclose} aria-label="Close details">×</button>
    </header>
    {#if hiddenByFilters}
      <p class="note">Hidden from the map and list by the current filters.</p>
    {/if}
    {#if a.emergency && a.emergency !== 'none'}
      <p class="note alert">Emergency status reported: {a.emergency}</p>
    {/if}
    <dl>
      <dt>Registration</dt>
      <dd>{a.registration ?? UNKNOWN}</dd>
      <dt>Callsign</dt>
      <dd>{a.callsign ?? UNKNOWN}</dd>
      <dt>ICAO address</dt>
      <dd class="mono">{a.id}</dd>
      <dt>Classification</dt>
      <dd>{classLabel(a.class)} <span class="muted">({classSource(a.class_rule)})</span></dd>
      <dt>Altitude (barometric)</dt>
      <dd>{a.on_ground ? 'On ground' : feet(a.altitude_baro_ft)}</dd>
      <dt>Altitude (GPS)</dt>
      <dd>{feet(a.altitude_geom_ft)}</dd>
      <dt>Ground speed</dt>
      <dd>{knots(a.ground_speed_kt)}</dd>
      <dt>Ground track</dt>
      <dd>{degrees(a.track_deg)}</dd>
      <dt>Vertical rate</dt>
      <dd>{verticalRate(a.vertical_rate_fpm)}</dd>
      <dt>From home</dt>
      <dd>{miles(a.distance_m)} {cardinal(a.bearing_deg)}{a.inside_visible ? '' : ' (outside visible area)'}</dd>
      <dt>Position age</dt>
      <dd>{seconds(a.position_age_s)} <span class="muted">({FRESHNESS[a.freshness] ?? a.freshness})</span></dd>
      <dt>Position source</dt>
      <dd>{positionSource(a.position_source)}</dd>
      <dt>Squawk</dt>
      <dd class="mono">{a.squawk ?? UNKNOWN}</dd>
      <dt>Last received</dt>
      <dd>{clock(a.last_received_at)}</dd>
    </dl>
    <p class="caveat">Barometric altitude is not height above ground. Ground track is not necessarily heading.</p>
    {#if exportHref}
      <a class="export" href={exportHref} download>Export this aircraft's tracks (GeoJSON)</a>
    {/if}
  </section>
{:else if lastSeen}
  <section class="detail" aria-label="Selected aircraft" data-testid="detail">
    <header>
      <div>
        <h2>{identity(lastSeen)}</h2>
        <p class="sub">No current position</p>
      </div>
      <button type="button" class="close" onclick={onclose} aria-label="Close details">×</button>
    </header>
    <p class="note">Last seen {clock(lastSeen.position_at ?? lastSeen.last_received_at)}. It may have left the area or dropped out of coverage.</p>
  </section>
{/if}

<style>
  .detail {
    padding: 12px;
    border-bottom: 1px solid #222b31;
    background: #12181c;
    font-size: 13px;
  }
  header {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
  }
  h2 {
    margin: 0;
    font-size: 17px;
    font-weight: 500;
  }
  .sub {
    margin: 2px 0 0;
    color: #8d99a2;
  }
  .close {
    padding: 0 6px;
    border: 0;
    background: none;
    color: #aab5bc;
    font-size: 22px;
    line-height: 1;
    cursor: pointer;
  }
  dl {
    display: grid;
    grid-template-columns: auto 1fr;
    gap: 4px 12px;
    margin: 10px 0 0;
  }
  dt {
    color: #8d99a2;
  }
  dd {
    margin: 0;
    font-variant-numeric: tabular-nums;
  }
  .mono {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  .muted {
    color: #8d99a2;
  }
  .note {
    margin: 8px 0 0;
    color: #c3ccd2;
  }
  .note.alert {
    color: #ff8a8a;
  }
  .export {
    display: inline-block;
    margin-top: 8px;
    color: #46c2ff;
    font-size: 12px;
  }
  .caveat {
    margin: 10px 0 0;
    color: #6f7c85;
    font-size: 12px;
  }
</style>
