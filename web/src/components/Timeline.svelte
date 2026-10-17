<script lang="ts">
  import { exportUrl } from '../lib/api';
  import { SPEEDS, type ReplayController } from '../lib/replay.svelte';
  import { bins, toTime, toX } from '../lib/timeline';

  let { replay }: { replay: ReplayController } = $props();

  let width = $state(0);
  let dragging = false;
  let track: SVGSVGElement;

  const tl = $derived(replay.timeline);
  const start = $derived(tl?.start ?? 0);
  const end = $derived(tl?.end ?? 0);
  const drawn = $derived(tl ? bins(tl, width) : []);
  const playheadX = $derived(toX(replay.at, start, end, width));

  const time = (ms: number) => new Date(ms).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', second: '2-digit' });
  const day = (ms: number) => new Date(ms).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });

  function seekFromPointer(e: PointerEvent) {
    const rect = track.getBoundingClientRect();
    replay.seek(toTime(e.clientX - rect.left, start, end, rect.width), { syncLocation: !dragging });
  }

  function down(e: PointerEvent) {
    replay.pause();
    dragging = true;
    track.setPointerCapture?.(e.pointerId);
    seekFromPointer(e);
  }

  function move(e: PointerEvent) {
    if (dragging) seekFromPointer(e);
  }

  function up(e: PointerEvent) {
    if (!dragging) return;
    dragging = false;
    seekFromPointer(e);
  }

  function keydown(e: KeyboardEvent) {
    if (e.key === 'ArrowLeft') replay.step(-1);
    else if (e.key === 'ArrowRight') replay.step(1);
    else return;
    e.preventDefault();
  }
</script>

<div class="timeline" data-testid="timeline">
  <div class="controls">
    <button type="button" onclick={() => replay.step(-1)} aria-label="Previous poll" title="Previous poll (←)">⏮</button>
    <button
      type="button"
      class="play"
      onclick={() => replay.toggle()}
      aria-label={replay.playing ? 'Pause' : 'Play'}
      title="Play or pause (space)"
      data-testid="play">{replay.playing ? '❚❚' : '▶'}</button
    >
    <button type="button" onclick={() => replay.step(1)} aria-label="Next poll" title="Next poll (→)">⏭</button>
    <label class="speed">
      <span class="visually-hidden">Speed</span>
      <select bind:value={replay.speed} data-testid="speed">
        {#each SPEEDS as s (s)}
          <option value={s}>{s}×</option>
        {/each}
      </select>
    </label>
    <span class="now" data-testid="replay-time">{tl ? `${day(replay.at)}, ${time(replay.at)}` : ''}</span>

    <label class="session">
      <span class="visually-hidden">Session</span>
      <select
        value={replay.sessionId}
        onchange={(e) => replay.selectSession(Number((e.currentTarget as HTMLSelectElement).value))}
        data-testid="session"
      >
        {#each replay.sessions as s (s.id)}
          <option value={s.id}>
            Session {s.id} · {s.first_poll_at ? day(Date.parse(s.first_poll_at)) : ''}
            {s.first_poll_at ? time(Date.parse(s.first_poll_at)) : ''} · {s.polls} polls
          </option>
        {/each}
      </select>
    </label>
    {#if replay.sessionId !== null}
      <details class="export">
        <summary>Export</summary>
        <div class="menu">
          <a href={exportUrl(replay.sessionId, { geometry: 'tracks' })} download>Tracks (GeoJSON)</a>
          <a href={exportUrl(replay.sessionId, { geometry: 'points' })} download>Points (GeoJSON)</a>
          <a href={exportUrl(replay.sessionId, { format: 'json' })} download>Observations (JSON)</a>
        </div>
      </details>
    {/if}
  </div>

  <div class="track-wrap" bind:clientWidth={width}>
    <svg
      bind:this={track}
      class="track"
      width={width}
      height="34"
      role="slider"
      tabindex="0"
      aria-label="Replay time"
      aria-valuemin={start}
      aria-valuemax={end}
      aria-valuenow={replay.at}
      aria-valuetext={tl ? time(replay.at) : ''}
      onpointerdown={down}
      onpointermove={move}
      onpointerup={up}
      onkeydown={keydown}
      data-testid="scrubber"
    >
      <defs>
        <pattern id="sleep" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <rect width="2" height="5" fill="#3a444b" />
        </pattern>
      </defs>
      <rect x="0" y="8" width={width} height="14" fill="#1a2126" rx="2" />
      {#each drawn as b, i (i)}
        {#if b.polls}
          <rect x={b.x} y="8" width={Math.max(1, b.width)} height="14" fill={b.failed ? '#d9534f' : '#2f5d4a'} />
        {/if}
        {#if b.rotorcraft}
          <rect x={b.x} y="25" width={Math.max(1, b.width)} height="4" fill="#ffb020" />
        {/if}
      {/each}
      {#each tl?.gaps ?? [] as g, i (i)}
        <rect
          x={toX(g.start, start, end, width)}
          y="8"
          width={Math.max(2, toX(g.end, start, end, width) - toX(g.start, start, end, width))}
          height="14"
          fill="url(#sleep)"
        >
          <title>Collector asleep, {time(g.start)} to {time(g.end)}</title>
        </rect>
      {/each}
      <line x1={playheadX} x2={playheadX} y1="2" y2="32" stroke="#46c2ff" stroke-width="2" />
    </svg>
    <div class="legend">
      <span>{tl ? time(start) : ''}</span>
      <span class="keys">
        <i class="k ok"></i>Polled <i class="k failed"></i>Failed <i class="k sleep"></i>Asleep
        <i class="k heli"></i>Helicopter (ADS-B A7)
      </span>
      <span>{tl ? time(end) : ''}</span>
    </div>
  </div>
</div>

<style>
  .timeline {
    padding: 8px 14px 6px;
    border-top: 1px solid #222b31;
    background: #0f1417;
    font-size: 13px;
  }
  .controls {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
    margin-bottom: 4px;
  }
  button,
  select,
  summary {
    padding: 4px 8px;
    border: 1px solid #36424a;
    border-radius: 4px;
    background: #161c20;
    color: #e6edf2;
    font: inherit;
    cursor: pointer;
  }
  .play {
    min-width: 40px;
  }
  .now {
    min-width: 150px;
    font-variant-numeric: tabular-nums;
  }
  .session {
    margin-left: auto;
  }
  .export {
    position: relative;
  }
  summary {
    list-style: none;
  }
  .menu {
    position: absolute;
    right: 0;
    bottom: calc(100% + 4px);
    z-index: 5;
    display: flex;
    flex-direction: column;
    min-width: 180px;
    padding: 4px 0;
    border: 1px solid #36424a;
    border-radius: 4px;
    background: #161c20;
  }
  .menu a {
    padding: 6px 12px;
    color: #e6edf2;
    text-decoration: none;
  }
  .menu a:hover {
    background: #1f282e;
  }
  .track {
    display: block;
    cursor: pointer;
    touch-action: none;
  }
  .track:focus-visible {
    outline: 2px solid #46c2ff;
  }
  .legend {
    display: flex;
    justify-content: space-between;
    color: #8d99a2;
    font-size: 11px;
  }
  .keys {
    display: flex;
    align-items: center;
    gap: 4px;
  }
  .k {
    display: inline-block;
    width: 10px;
    height: 8px;
    margin-left: 8px;
  }
  .k.ok {
    background: #2f5d4a;
  }
  .k.failed {
    background: #d9534f;
  }
  .k.sleep {
    background: repeating-linear-gradient(45deg, #3a444b 0 2px, transparent 2px 4px);
  }
  .k.heli {
    height: 4px;
    background: #ffb020;
  }
</style>
