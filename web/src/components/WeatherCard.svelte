<script lang="ts">
  import { miles } from '../lib/format';
  import { observedAgo, observedClock, sky, temperature, WEATHER_STALE_S, wind } from '../lib/weather';
  import type { Weather } from '../lib/types';

  let { weather, now, compact = false }: { weather: Weather; now: number; compact?: boolean } = $props();

  const obs = $derived(weather.observation);
  const ageS = $derived(obs ? Math.max(0, (now - Date.parse(obs.observed_at)) / 1000) : null);
  const stale = $derived(ageS !== null && ageS > WEATHER_STALE_S);
  const summary = $derived.by(() => {
    if (!obs) return weather.status === 'starting' ? 'Checking…' : 'Unavailable';
    const conditions = [temperature(obs), obs.description].filter(Boolean).join(' ');
    const steadyWind = wind({ ...obs, wind_gust_mph: null });
    return [conditions, steadyWind].filter(Boolean).join(' · ') || 'Observation available';
  });
  const lines = $derived(obs ? [wind(obs), sky(obs)].filter((l): l is string => l !== null) : []);
  const station = $derived(weather.station);
</script>

<details class="weather" class:compact class:stale data-testid="weather">
  <summary>
    <span class="label">Weather</span>{' '}
    {#if stale}<span class="status">Stale · </span>{/if}{summary}
  </summary>
  <div class="reading">
    {#each lines as line (line)}
      <div>{line}</div>
    {/each}
    {#if obs}
      <div class="age">
        <time datetime={obs.observed_at}>{observedClock(obs.observed_at)}</time>
        · {observedAgo(ageS ?? 0)}{#if stale}, station not reporting{/if}
      </div>
    {/if}
    <div class="station">
      {#if station}
        NWS · {station.id}{#if station.name} · {station.name}{/if}
        {#if station.distance_m !== null} · {miles(station.distance_m)} from home{/if}
      {:else}
        {weather.source.name}
      {/if}
    </div>
  </div>
</details>

<style>
  .weather {
    border-top: 1px solid var(--line);
    background: var(--panel);
    color: var(--text-soft);
    font-size: 12px;
    font-variant-numeric: tabular-nums;
  }
  .compact {
    position: absolute;
    /* The map controls keep their size when the weather text scales. */
    top: calc(10px / var(--ui-scale, 1));
    right: calc(52px / var(--ui-scale, 1));
    z-index: 2;
    max-width: calc(100% - 150px / var(--ui-scale, 1));
    border: 1px solid var(--control-line);
    border-radius: 4px;
    background: rgba(5, 12, 8, 0.9);
    zoom: var(--ui-scale, 1);
  }
  summary {
    overflow: hidden;
    padding: 8px 12px;
    cursor: pointer;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  summary:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: -2px;
  }
  details[open] summary {
    white-space: normal;
  }
  .label {
    margin-right: 4px;
    color: var(--muted-strong);
  }
  .reading {
    display: grid;
    gap: 3px;
    padding: 0 12px 9px;
  }
  .age,
  .station {
    color: var(--muted-strong);
  }
  .status,
  .stale .age {
    color: var(--amber);
  }
</style>
