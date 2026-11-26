<script lang="ts">
  import { miles, wallClockPt } from '../lib/format';
  import { observedAgo, observedClock, sky, temperature, WEATHER_STALE_S, wind } from '../lib/weather';
  import type { Weather } from '../lib/types';

  let { weather, now, compact = false, readOnly = false }: {
    weather: Weather; now: number; compact?: boolean; readOnly?: boolean;
  } = $props();

  const obs = $derived(weather.observation);
  const ageS = $derived(obs ? Math.max(0, (now - Date.parse(obs.observed_at)) / 1000) : null);
  const stale = $derived(ageS !== null && ageS > WEATHER_STALE_S);
  const temp = $derived(obs ? temperature(obs) : null);
  const conditions = $derived(obs ? [temp, obs.description].filter(Boolean).join(' ') : '');
  const summary = $derived.by(() => {
    if (!obs) return weather.status === 'starting' ? 'Checking…' : 'Unavailable';
    const steadyWind = wind({ ...obs, wind_gust_mph: null });
    return [conditions, steadyWind].filter(Boolean).join(' · ') || 'Observation available';
  });
  const lines = $derived(obs ? [wind(obs), sky(obs)].filter((l): l is string => l !== null) : []);
  const station = $derived(weather.station);
  const clock = $derived(wallClockPt(now));
</script>

{#snippet headline(value: string)}
  {#if stale}<span class="status">Stale · </span>{/if}{value}
{/snippet}

{#snippet details()}
  <div class="reading">
    {#each lines as line (line)}
      <div>{line}</div>
    {/each}
    {#if obs && !readOnly}
      <div class="age">
        <time datetime={obs.observed_at}>{observedClock(obs.observed_at)}</time>
        · {observedAgo(ageS ?? 0)}{#if stale}, station not reporting{/if}
      </div>
    {/if}
    <div class="station">
      {#if station}
        NWS · {station.id}{#if !readOnly && station.name} · {station.name}{/if}
        {#if !readOnly && station.distance_m !== null} · {miles(station.distance_m)} from home{/if}
      {:else}
        {weather.source.name}
      {/if}
      {#if readOnly && obs} · <time datetime={obs.observed_at}>{observedAgo(ageS ?? 0)}</time>{/if}
    </div>
  </div>
{/snippet}

{#if readOnly}
  <section class="weather roomy" class:stale aria-label="Weather" data-testid="weather">
    <div class="topline">
      <div class="temp" aria-hidden={temp ? undefined : true}>{temp ?? '—'}</div>
      <div class="beside">
        <time class="clock" datetime={new Date(now).toISOString()}>{clock}</time>
        <div class="desc">
          {#if stale}<span class="status">Stale · </span>{/if}
          {obs ? (obs.description || 'Observation available') : summary}
        </div>
      </div>
    </div>
    {@render details()}
  </section>
{:else}
  <details class="weather" class:compact class:stale aria-label="Weather" data-testid="weather">
    <summary>{@render headline(summary)}</summary>
    {@render details()}
  </details>
{/if}

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
  .roomy {
    padding: 8px 14px 6px;
  }
  .topline {
    display: flex;
    align-items: baseline;
    gap: 14px;
    min-width: 0;
  }
  .temp {
    flex: none;
    color: var(--text);
    font-family: var(--mono);
    font-size: 42px;
    font-weight: 500;
    letter-spacing: -0.03em;
    line-height: 1;
  }
  .beside {
    display: grid;
    gap: 2px;
    min-width: 0;
  }
  .clock {
    color: var(--text);
    font-family: var(--mono);
    font-size: 18px;
    font-weight: 500;
    letter-spacing: 0.02em;
  }
  .desc {
    color: var(--text-soft);
    font-size: 13px;
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
  .reading {
    display: grid;
    gap: 3px;
    padding: 0 12px 9px;
  }
  .roomy .reading {
    padding: 6px 0 0;
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
