<script lang="ts">
  import { miles } from '../lib/format';
  import { observedAgo, observedClock, sky, temperature, WEATHER_STALE_S, wind } from '../lib/weather';
  import type { Weather } from '../lib/types';

  let { weather, now }: { weather: Weather; now: number } = $props();

  const obs = $derived(weather.observation);
  const ageS = $derived(obs ? Math.max(0, (now - Date.parse(obs.observed_at)) / 1000) : null);
  const stale = $derived(ageS !== null && ageS > WEATHER_STALE_S);
  const lines = $derived(obs ? [wind(obs), sky(obs)].filter((l): l is string => l !== null) : []);
  const station = $derived(weather.station);
  const detail = $derived(
    station
      ? `${station.name ?? station.id}${station.distance_m !== null ? `, ${miles(station.distance_m)} from home` : ''} · ${weather.source.name}`
      : weather.source.name,
  );
</script>

<section class="weather" class:stale data-testid="weather" aria-label="Weather near home" title={detail}>
  <header>
    <span>Weather</span>
    {#if station}<span class="station">NWS · {station.id}</span>{/if}
  </header>
  {#if obs}
    <div class="now">
      {#if temperature(obs)}<span class="temp">{temperature(obs)}</span>{/if}
      {#if obs.description}<span class="desc">{obs.description}</span>{/if}
    </div>
    {#each lines as line (line)}
      <div class="line">{line}</div>
    {/each}
    <div class="age">
      {observedClock(obs.observed_at)} · {observedAgo(ageS ?? 0)}{#if stale}, station not reporting{/if}
    </div>
  {:else}
    <div class="age">{weather.status === 'starting' ? 'Checking…' : 'Unavailable'}</div>
  {/if}
</section>

<style>
  .weather {
    position: absolute;
    top: 10px;
    right: 50px;
    z-index: 2;
    min-width: 210px;
    padding: 8px 12px 9px;
    border: 1px solid var(--control-line);
    border-radius: 4px;
    background: rgba(5, 12, 8, 0.9);
    color: var(--text-soft);
    font-size: 13px;
    font-variant-numeric: tabular-nums;
    zoom: var(--ui-scale, 1);
  }
  header {
    display: flex;
    justify-content: space-between;
    gap: 12px;
    color: var(--muted);
    font-size: 11px;
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }
  .station {
    font-family: var(--mono);
  }
  .now {
    display: flex;
    align-items: baseline;
    gap: 8px;
    margin: 2px 0 1px;
  }
  .temp {
    color: var(--text);
    font-family: var(--mono);
    font-size: 30px;
    font-weight: 500;
    line-height: 1.1;
  }
  .desc {
    color: var(--text);
    font-size: 15px;
  }
  .age {
    margin-top: 3px;
    color: var(--muted);
    font-size: 12px;
  }
  .stale .now,
  .stale .line {
    opacity: 0.5;
  }
  .stale .age {
    color: var(--amber);
  }
</style>
