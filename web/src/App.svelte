<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import AircraftList from './components/AircraftList.svelte';
  import DetailPanel from './components/DetailPanel.svelte';
  import FeedStatus from './components/FeedStatus.svelte';
  import FiltersPanel from './components/Filters.svelte';
  import MapView from './components/Map.svelte';
  import { ApiError, replayTarget, startPolling } from './lib/api';
  import { applyFilters, defaultFilters, passes, type Filters } from './lib/filters';
  import { miles } from './lib/format';
  import { ageAircraft } from './lib/freshness';
  import { emptyListMessage, statusView } from './lib/status';
  import type { Snapshot } from './lib/types';

  interface Props {
    fetchFn?: typeof fetch;
    pollMs?: number;
    tickMs?: number;
  }

  let { fetchFn = (...args) => fetch(...args), pollMs, tickMs = 1000 }: Props = $props();

  const replay = replayTarget(window.location.search);

  let snapshot = $state<Snapshot | null>(null);
  let error = $state<ApiError | null>(null);
  let fetchedAt = $state(0);
  let now = $state(Date.now());
  let selectedId = $state<string | null>(null);
  let filters = $state<Filters>(defaultFilters());
  let filtersInitialized = false;

  // Seconds since the snapshot arrived; replay is a fixed moment and doesn't age
  const elapsedS = $derived(snapshot && !replay ? Math.max(0, (now - fetchedAt) / 1000) : 0);
  const aged = $derived(snapshot ? ageAircraft(snapshot.aircraft, elapsedS, snapshot.thresholds) : []);
  const view = $derived(statusView(snapshot, error, elapsedS));
  const onMap = $derived(applyFilters(aged, filters));
  const listed = $derived(
    (filters.listVisibleOnly ? onMap.filter((a) => a.inside_visible) : [...onMap]).sort((a, b) => a.distance_m - b.distance_m),
  );
  const selected = $derived(aged.find((a) => a.id === selectedId) ?? null);
  const selectedLastSeen = $derived(
    !selected && selectedId ? (snapshot?.last_seen.find((a) => a.id === selectedId) ?? null) : null,
  );
  const visibleLabel = $derived(snapshot ? miles(snapshot.area.visible_radius_m) : 'visible area');
  const attribution = $derived(snapshot?.source);

  let stop: (() => void) | undefined;
  let ticker: ReturnType<typeof setInterval> | undefined;

  onMount(() => {
    stop = startPolling(
      (s) => {
        snapshot = s;
        fetchedAt = Date.now();
        now = fetchedAt;
        error = null;
        if (!filtersInitialized) {
          filters.maxAltitudeFt = s.area.max_altitude_baro_ft;
          filtersInitialized = true;
        }
      },
      (e) => (error = e),
      { replay, fetchFn, ...(pollMs ? { intervalMs: pollMs } : {}) },
    );
    ticker = setInterval(() => (now = Date.now()), tickMs);
  });

  onDestroy(() => {
    stop?.();
    clearInterval(ticker);
  });

  function select(id: string | null) {
    selectedId = id;
  }
</script>

<div class="app">
  <header class="bar">
    <h1>Airspace</h1>
    <FeedStatus {view} />
    {#if snapshot && !view.untrusted}
      <span class="count" data-testid="count">
        {listed.length} aircraft{filters.listVisibleOnly ? ` within ${visibleLabel}` : ''}
      </span>
    {/if}
  </header>

  <main>
    <div class="map-pane" class:untrusted={view.untrusted}>
      <MapView aircraft={onMap} area={snapshot?.area ?? null} {selectedId} onselect={select} />
    </div>
    <aside>
      <FiltersPanel bind:filters {visibleLabel} />
      <DetailPanel
        aircraft={selected}
        lastSeen={selectedLastSeen}
        hiddenByFilters={selected !== null && !passes(selected, filters)}
        onclose={() => select(null)}
      />
      <div class="list-wrap">
        <AircraftList
          aircraft={listed}
          {selectedId}
          emptyMessage={emptyListMessage(view, aged.length > 0)}
          onselect={select}
        />
      </div>
      <footer>
        Data:
        {#if attribution?.url}
          <a href={attribution.url} target="_blank" rel="noreferrer">{attribution.name ?? attribution.provider}</a>
        {:else}
          {attribution?.provider ?? 'unknown source'}
        {/if}
        · personal, noncommercial use
      </footer>
    </aside>
  </main>
</div>

<style>
  :global(html, body) {
    height: 100%;
    margin: 0;
    background: #0d1114;
    color: #e6edf2;
    font-family: Roboto, system-ui, -apple-system, 'Segoe UI', sans-serif;
  }
  :global(#app) {
    height: 100%;
  }
  :global(.visually-hidden) {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
    white-space: nowrap;
  }
  .app {
    display: flex;
    flex-direction: column;
    height: 100%;
  }
  .bar {
    display: flex;
    align-items: center;
    gap: 14px;
    padding: 8px 14px;
    border-bottom: 1px solid #222b31;
  }
  h1 {
    margin: 0;
    font-size: 16px;
    font-weight: 500;
  }
  .count {
    margin-left: auto;
    flex: none;
    color: #aab5bc;
    font-size: 13px;
  }
  main {
    display: flex;
    flex: 1;
    min-height: 0;
  }
  .map-pane {
    position: relative;
    flex: 1;
    min-width: 0;
  }
  /* When data isn't current, mute the map so it can't pass for live */
  .map-pane.untrusted {
    filter: grayscale(1) brightness(0.7);
  }
  aside {
    display: flex;
    flex-direction: column;
    width: 340px;
    border-left: 1px solid #222b31;
  }
  .list-wrap {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
  }
  footer {
    padding: 8px 12px;
    border-top: 1px solid #222b31;
    color: #6f7c85;
    font-size: 12px;
  }
  footer a {
    color: #8d99a2;
  }
  @media (max-width: 760px) {
    main {
      flex-direction: column;
    }
    .map-pane {
      flex: none;
      height: 55vh;
    }
    aside {
      flex: 1;
      width: auto;
      min-height: 0;
      border-left: 0;
    }
    .bar {
      flex-wrap: wrap;
    }
  }
</style>
