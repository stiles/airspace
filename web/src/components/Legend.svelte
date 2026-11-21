<script lang="ts">
  import { untrack } from 'svelte';
  import { loadPref, savePref } from '../lib/prefs';

  let {
    visibleLabel,
    compact = false,
    outlineNames = [],
    readOnly = false,
  }: { visibleLabel: string; compact?: boolean; outlineNames?: string[]; readOnly?: boolean } = $props();

  // The map remounts the legend when the layout changes. The old desktop key stored its open default.
  const key = untrack(() => (compact ? 'legend-compact' : 'legend-expanded'));
  const initiallyOpen = untrack(() => !readOnly && loadPref(key, false));

  function toggle(e: Event) {
    savePref(key, (e.currentTarget as HTMLDetailsElement).open);
  }

  // Same outlines as the map symbols in lib/icons.ts (64-unit canvas)
  const PLANE = 'M32 6 36 14 36 26 56 38 56 43 36 37 35 50 43 56 43 59 32 56 21 59 21 56 29 50 28 37 8 43 8 38 28 26 28 14Z';
</script>

{#snippet plane(cls: string)}
  <svg viewBox="0 0 64 64" class="sym {cls}" aria-hidden="true"><path d={PLANE} /></svg>
{/snippet}

{#snippet entries()}
  <ul>
    <li>{@render plane('aircraft')} Airplane</li>
    <li>
      <svg viewBox="0 0 64 64" class="sym heli" aria-hidden="true">
        <path d="M12 12 52 52M52 12 12 52" class="rotor" />
        <ellipse cx="32" cy="30" rx="9" ry="12" />
        <rect x="30" y="40" width="4" height="18" />
        <rect x="25" y="55" width="14" height="4" />
      </svg>
      Helicopter
    </li>
    <li>{@render plane('unknown')} Type unknown</li>
    <li>
      <svg viewBox="0 0 64 64" class="sym aircraft" aria-hidden="true">
        <circle cx="32" cy="32" r="9" />
        <circle cx="32" cy="32" r="17" class="outline" />
      </svg>
      {readOnly ? 'No track' : 'No track reported'}
    </li>
    <li>{@render plane('aircraft fading')} Position aging</li>
    {#if !readOnly}
      <li>
        <svg viewBox="0 0 64 64" class="sym selected" aria-hidden="true">
          <circle cx="32" cy="32" r="26" class="outline" />
          <path d={PLANE} transform="translate(13 13) scale(0.6)" />
        </svg>
        Selected
      </li>
    {/if}
    <li><i class="line trail" aria-hidden="true"></i> Recent track</li>
    <li><i class="orbit" aria-hidden="true"></i> {readOnly ? 'Orbit or hover' : 'Circling or hovering (inferred)'}</li>
    <li class="gap"><i class="dot" aria-hidden="true"></i> Home</li>
    <li><i class="line ring" aria-hidden="true"></i> 1-mile rings</li>
    <li><i class="line visible" aria-hidden="true"></i> {readOnly ? `Home ring ${visibleLabel}` : `Home radius, ${visibleLabel}`}</li>
    <li><i class="line collection" aria-hidden="true"></i> Collection limit</li>
    {#each outlineNames as name (name)}
      <li><i class="line outline" aria-hidden="true"></i> {name}</li>
    {/each}
    <li><span class="alt" aria-hidden="true">015</span> {readOnly ? '1,500 ft baro' : '1,500 ft (barometric)'}</li>
  </ul>
{/snippet}

{#if readOnly}
  <section class="static-legend" data-testid="legend">
    <h2>Legend</h2>
    {@render entries()}
  </section>
{:else}
  <details class="legend" open={initiallyOpen} ontoggle={toggle} data-testid="legend">
    <summary>Legend</summary>
    {@render entries()}
  </details>
{/if}

<style>
  .legend {
    position: absolute;
    bottom: 34px;
    left: 10px;
    z-index: 2;
    /* Under zoom, the percentage resolves against the map and the px offsets scale */
    max-height: calc(100% - 90px);
    zoom: var(--ui-scale, 1);
    overflow-y: auto;
    border: 1px solid var(--control-line);
    border-radius: 4px;
    background: rgba(5, 12, 8, 0.88);
    color: var(--text-soft);
    font-size: 12px;
  }
  .static-legend {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr);
    gap: 12px;
    padding: 6px 12px;
    border-top: 1px solid var(--line);
    background: var(--panel);
    color: var(--text-soft);
    font-size: 12px;
    zoom: var(--ui-scale, 1);
  }
  .static-legend h2 {
    margin: 0;
    padding-top: 2px;
    color: var(--muted-strong);
    font-size: inherit;
    font-weight: 600;
  }
  .static-legend ul {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 100px), 1fr));
    gap: 2px 10px;
    padding: 0;
  }
  .static-legend li {
    white-space: normal;
    overflow-wrap: anywhere;
  }
  .static-legend li.gap {
    margin: 0;
    padding: 0;
    border: 0;
  }
  @media (max-height: 540px) {
    .static-legend {
      padding-block: 3px;
    }
    .static-legend ul {
      grid-template-columns: repeat(auto-fit, minmax(min(100%, 80px), 1fr));
      column-gap: 6px;
    }
  }
  summary {
    padding: 5px 10px;
    color: var(--muted-strong);
    cursor: pointer;
    user-select: none;
  }
  summary:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: -2px;
  }
  ul {
    margin: 0;
    padding: 2px 10px 8px;
    list-style: none;
  }
  li {
    display: flex;
    align-items: center;
    gap: 8px;
    min-height: 20px;
    white-space: nowrap;
  }
  li.gap {
    margin-top: 6px;
    padding-top: 6px;
    border-top: 1px solid var(--line-soft);
  }
  .sym {
    flex: none;
    width: 16px;
    height: 16px;
    fill: currentColor;
    stroke: currentColor;
    stroke-width: 0;
  }
  .sym .rotor,
  .sym .outline {
    fill: none;
    stroke-width: 4;
    stroke-linecap: round;
  }
  .sym.aircraft {
    color: var(--phosphor);
  }
  .sym.heli {
    color: var(--amber);
  }
  .sym.unknown {
    color: var(--unknown);
  }
  .sym.fading {
    opacity: 0.4;
  }
  .sym.selected {
    color: var(--accent);
  }
  .line {
    flex: none;
    width: 16px;
    height: 0;
    border-top: 1px solid var(--ring);
  }
  .line.trail {
    height: 2px;
    border: 0;
    border-radius: 1px;
    background: linear-gradient(90deg, transparent, var(--phosphor));
  }
  .line.visible {
    border-top: 1.5px solid var(--phosphor);
    opacity: 0.7;
  }
  .line.collection {
    border-top-style: dashed;
  }
  .line.outline {
    border-top: 2px solid var(--outline);
    box-shadow: 0 0 4px var(--outline);
  }
  .orbit {
    flex: none;
    box-sizing: border-box;
    width: 14px;
    height: 14px;
    margin: 0 1px;
    border: 1.5px dashed var(--amber);
    border-radius: 50%;
  }
  .dot {
    flex: none;
    box-sizing: border-box;
    width: 10px;
    height: 10px;
    margin: 0 3px;
    border: 2px solid var(--bg);
    border-radius: 50%;
    background: var(--accent);
    box-shadow: 0 0 0 1px var(--accent);
  }
  .alt {
    flex: none;
    width: 16px;
    color: #a6f7c6;
    font-family: var(--mono);
    font-size: 9px;
    letter-spacing: -0.03em;
  }
</style>
