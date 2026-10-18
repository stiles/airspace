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
  import { loadPref, savePref } from './lib/prefs';
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
  // Map-only layout for a small always-on window; small windows start compact
  let compact = $state(loadPref('compact', window.innerWidth < 560 || window.innerHeight < 420));

  function toggleCompact() {
    compact = !compact;
    savePref('compact', compact);
  }

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

{#snippet credit()}
  Data:
  {#if attribution?.url}
    <a href={attribution.url} target="_blank" rel="noreferrer">{attribution.name ?? attribution.provider}</a>
  {:else}
    {attribution?.provider ?? 'unknown source'}
  {/if}
  · personal, noncommercial use
{/snippet}

<div class="app" class:compact>
  <header class="bar">
    <h1>Airspace</h1>
    {#if !replayOnly}
      <div class="modes" role="group" aria-label="Mode">
        <button type="button" aria-pressed={mode === 'live'} onclick={enterLive}>Live</button>
        <button type="button" aria-pressed={mode === 'replay'} onclick={() => mode !== 'replay' && enterReplay()}>Replay</button>
      </div>
    {/if}
    <FeedStatus {view} />
    <div class="right">
    {#if snapshot && !view.untrusted}
      <span class="count" data-testid="count">
        {listed.length} aircraft{filters.listVisibleOnly ? ` within ${visibleLabel}` : ''}
      </span>
    {/if}
    <button
      type="button"
      class="layout"
      aria-pressed={compact}
      onclick={toggleCompact}
      title={compact ? 'Show the list and filters' : 'Map only, for a small window'}
      data-testid="compact-toggle">{compact ? 'Expand' : 'Compact'}</button
    >
    </div>
  </header>

  <main>
    <div class="map-column">
      <div class="map-pane" class:untrusted={view.untrusted}>
        <MapView aircraft={onMap} area={snapshot?.area ?? null} {selectedId} onselect={select} {compact} />
      </div>
      {#if mode === 'replay'}
        <Timeline {replay} {compact} />
      {/if}
    </div>
    {#if compact}
      <footer class="credit-bar">{@render credit()}</footer>
    {:else}
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
        <footer>{@render credit()}</footer>
      </aside>
    {/if}
  </main>
</div>

<style>
  /* Radar-scope palette; map colors in lib/theme.ts use the same values */
  :global(:root) {
    --bg: #050a07;
    --panel: #08110c;
    --panel-raised: #0b1711;
    --line: #143022;
    --line-soft: #0f2219;
    --control-bg: #0b1610;
    --control-line: #1f4a33;
    --control-line-hover: #2f6e4c;
    --hover: #0f2018;
    --selected-bg: #0f2c2f;
    --text: #d4f7e2;
    --text-soft: #a9d8bd;
    --muted-strong: #86b89c;
    --muted: #5f9077;
    --faint: #4a705c;
    --phosphor: #3dff8f;
    --accent: #7fe7ff;
    --amber: #ffb020;
    --unknown: #5c8c72;
    --surface: #3c5a4a;
    --ring: #1f7a4a;
    --track: #0b1a12;
    --ok: #1d6b42;
    --sleep: #26392f;
    --alert: #ff5a5a;
    --alert-bg: #5c1a1a;
    --alert-text: #f3d0d0;
    --mono: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
    color-scheme: dark;
  }
  :global(html, body) {
    height: 100%;
    margin: 0;
    background: var(--bg);
    color: var(--text);
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
    border-bottom: 1px solid var(--line);
  }
  h1 {
    margin: 0;
    color: var(--phosphor);
    font-family: var(--mono);
    font-size: 15px;
    font-weight: 500;
    letter-spacing: 0.04em;
  }
  .right {
    display: flex;
    flex: none;
    align-items: center;
    gap: 12px;
    margin-left: auto;
  }
  .count {
    flex: none;
    color: var(--muted-strong);
    font-size: 13px;
  }
  .layout {
    flex: none;
    padding: 4px 10px;
    border: 1px solid var(--control-line);
    border-radius: 4px;
    background: var(--control-bg);
    color: var(--muted-strong);
    font: inherit;
    font-size: 13px;
    cursor: pointer;
  }
  .layout:hover {
    border-color: var(--control-line-hover);
    color: var(--text);
  }
  /* Compact: one slim bar over the map, nothing else */
  .compact .bar {
    flex-wrap: nowrap;
    gap: 10px;
    padding: 5px 10px;
  }
  .compact .modes {
    display: none;
  }
  .compact h1 {
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
    border: 1px solid var(--control-line);
    border-radius: 4px;
    overflow: hidden;
  }
  .modes button {
    padding: 4px 10px;
    border: 0;
    background: var(--control-bg);
    color: var(--muted-strong);
    font: inherit;
    font-size: 13px;
    cursor: pointer;
  }
  .modes button[aria-pressed='true'] {
    background: var(--selected-bg);
    color: var(--text);
  }
  /* When data isn't current, mute the map so it can't pass for live */
  .map-pane.untrusted {
    filter: grayscale(1) brightness(0.7);
  }
  aside {
    display: flex;
    flex-direction: column;
    width: 340px;
    border-left: 1px solid var(--line);
  }
  .list-wrap {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
  }
  footer {
    padding: 8px 12px;
    border-top: 1px solid var(--line);
    color: var(--faint);
    font-size: 12px;
  }
  footer a {
    color: var(--muted);
  }
  .credit-bar {
    position: absolute;
    right: 0;
    bottom: 0;
    left: 0;
    padding: 3px 10px;
    border-top: 0;
    font-size: 11px;
  }
  .compact main {
    position: relative;
  }
  .compact .map-column {
    padding-bottom: 20px;
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
    .compact .map-column {
      flex: 1;
      height: auto;
    }
  }
</style>
