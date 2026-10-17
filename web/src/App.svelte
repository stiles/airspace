<script lang="ts">
  import { onDestroy, onMount, untrack } from 'svelte';
  import AircraftList from './components/AircraftList.svelte';
  import DetailPanel from './components/DetailPanel.svelte';
  import FeedStatus from './components/FeedStatus.svelte';
  import FiltersPanel from './components/Filters.svelte';
  import MapView from './components/Map.svelte';
  import Timeline from './components/Timeline.svelte';
  import { ApiError, exportUrl, replayTarget, startPolling } from './lib/api';
  import { applyFilters, defaultFilters, passes, type Filters } from './lib/filters';
  import { miles } from './lib/format';
  import { ageAircraft } from './lib/freshness';
  import { ReplayController } from './lib/replay.svelte';
  import { emptyListMessage, statusView } from './lib/status';
  import type { Snapshot } from './lib/types';

  interface Props {
    fetchFn?: typeof fetch;
    pollMs?: number;
    tickMs?: number;
    replayTickMs?: number;
  }

  let { fetchFn = (...args) => fetch(...args), pollMs, tickMs = 1000, replayTickMs }: Props = $props();

  type Mode = 'live' | 'replay';
  let mode = $state<Mode>('live');
  // Set when the server runs `airspace replay`: no live state to switch back to
  let replayOnly = $state(false);

  let liveSnapshot = $state<Snapshot | null>(null);
  let liveError = $state<ApiError | null>(null);
  let fetchedAt = $state(0);
  let now = $state(Date.now());
  let selectedId = $state<string | null>(null);
  let filters = $state<Filters>(defaultFilters());
  let filtersInitialized = false;

  const replay = new ReplayController({
    fetchFn: (...args) => fetchFn(...args),
    tickMs: untrack(() => replayTickMs),
    onLocation: (sessionId, at) => setLocation({ session: String(sessionId), at }),
  });

  const snapshot = $derived(mode === 'live' ? liveSnapshot : replay.snapshot);
  const error = $derived(mode === 'live' ? liveError : replay.error);
  // Seconds since the live snapshot arrived; a replayed moment doesn't age
  const elapsedS = $derived(mode === 'live' && snapshot ? Math.max(0, (now - fetchedAt) / 1000) : 0);
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
  const sessionId = $derived(mode === 'live' ? (liveSnapshot?.session_id ?? null) : replay.sessionId);

  let stopLive: (() => void) | undefined;
  let ticker: ReturnType<typeof setInterval> | undefined;

  function setLocation(params: Record<string, string> | null) {
    const url = new URL(window.location.href);
    url.search = params ? `?${new URLSearchParams(params)}` : '';
    window.history.replaceState(null, '', url);
  }

  function onSnapshot(s: Snapshot) {
    if (!filtersInitialized) {
      filters.maxAltitudeFt = s.area.max_altitude_baro_ft;
      filtersInitialized = true;
    }
  }

  function startLive() {
    stopLive?.();
    stopLive = startPolling(
      (s) => {
        liveSnapshot = s;
        fetchedAt = Date.now();
        now = fetchedAt;
        liveError = null;
        onSnapshot(s);
      },
      (e) => {
        if (e.status === 409) {
          // The server is in replay mode
          replayOnly = true;
          enterReplay();
          return;
        }
        liveError = e;
      },
      { fetchFn, ...(pollMs ? { intervalMs: pollMs } : {}) },
    );
  }

  function enterReplay(sessionId?: number | null, at?: number | null) {
    stopLive?.();
    stopLive = undefined;
    mode = 'replay';
    replay.open(sessionId ?? liveSnapshot?.session_id ?? null, at ?? null);
  }

  function enterLive() {
    if (replayOnly) return;
    replay.pause();
    mode = 'live';
    setLocation(null);
    startLive();
  }

  $effect(() => {
    if (replay.snapshot) onSnapshot(replay.snapshot);
  });

  onMount(() => {
    const target = replayTarget(window.location.search);
    if (target) enterReplay(target.sessionId, Date.parse(target.at));
    else startLive();
    ticker = setInterval(() => (now = Date.now()), tickMs);
  });

  onDestroy(() => {
    stopLive?.();
    replay.close();
    clearInterval(ticker);
  });

  function select(id: string | null) {
    selectedId = id;
  }

  function keydown(e: KeyboardEvent) {
    if (mode !== 'replay' || e.key !== ' ') return;
    const tag = (e.target as HTMLElement | null)?.tagName;
    if (tag && ['INPUT', 'SELECT', 'BUTTON', 'TEXTAREA', 'SUMMARY', 'A'].includes(tag)) return;
    e.preventDefault();
    replay.toggle();
  }
</script>

<svelte:window onkeydown={keydown} />

<div class="app">
  <header class="bar">
    <h1>Airspace</h1>
    {#if !replayOnly}
      <div class="modes" role="group" aria-label="Mode">
        <button type="button" aria-pressed={mode === 'live'} onclick={enterLive}>Live</button>
        <button type="button" aria-pressed={mode === 'replay'} onclick={() => mode !== 'replay' && enterReplay()}>Replay</button>
      </div>
    {/if}
    <FeedStatus {view} />
    {#if snapshot && !view.untrusted}
      <span class="count" data-testid="count">
        {listed.length} aircraft{filters.listVisibleOnly ? ` within ${visibleLabel}` : ''}
      </span>
    {/if}
  </header>

  <main>
    <div class="map-column">
      <div class="map-pane" class:untrusted={view.untrusted}>
        <MapView aircraft={onMap} area={snapshot?.area ?? null} {selectedId} onselect={select} />
      </div>
      {#if mode === 'replay'}
        <Timeline {replay} />
      {/if}
    </div>
    <aside>
      <FiltersPanel bind:filters {visibleLabel} />
      <DetailPanel
        aircraft={selected}
        lastSeen={selectedLastSeen}
        hiddenByFilters={selected !== null && !passes(selected, filters)}
        exportHref={sessionId !== null && selectedId ? exportUrl(sessionId, { aircraft: selectedId }) : null}
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
    font-family: Inter, system-ui, -apple-system, 'Segoe UI', sans-serif;
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
  .map-column {
    display: flex;
    flex: 1;
    flex-direction: column;
    min-width: 0;
  }
  .map-pane {
    position: relative;
    flex: 1;
    min-height: 0;
  }
  .modes {
    display: flex;
    flex: none;
    border: 1px solid #36424a;
    border-radius: 4px;
    overflow: hidden;
  }
  .modes button {
    padding: 4px 10px;
    border: 0;
    background: #161c20;
    color: #aab5bc;
    font: inherit;
    font-size: 13px;
    cursor: pointer;
  }
  .modes button[aria-pressed='true'] {
    background: #12303f;
    color: #e6edf2;
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
    .map-column {
      flex: none;
      height: 60vh;
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
