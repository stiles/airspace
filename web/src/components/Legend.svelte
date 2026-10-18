<script lang="ts">
  import { untrack } from 'svelte';
  import { loadPref, savePref } from '../lib/prefs';

  let { visibleLabel, compact = false }: { visibleLabel: string; compact?: boolean } = $props();

  // The map remounts the legend when the layout changes, so the layout is fixed here.
  // Open on a full window, folded in compact mode, unless the user chose otherwise.
  const folded = untrack(() => compact);
  const key = folded ? 'legend-compact' : 'legend';
  let open = $state(loadPref(key, !folded));

  function toggle(e: Event) {
    open = (e.currentTarget as HTMLDetailsElement).open;
    savePref(key, open);
  }

  // Same outlines as the map symbols in lib/icons.ts (64-unit canvas)
  const PLANE = 'M32 6 36 14 36 26 56 38 56 43 36 37 35 50 43 56 43 59 32 56 21 59 21 56 29 50 28 37 8 43 8 38 28 26 28 14Z';
</script>

{#snippet plane(cls: string)}
  <svg viewBox="0 0 64 64" class="sym {cls}" aria-hidden="true"><path d={PLANE} /></svg>
{/snippet}

<details class="legend" {open} ontoggle={toggle} data-testid="legend">
  <summary>Legend</summary>
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
      No track reported
    </li>
    <li>{@render plane('aircraft fading')} Position aging</li>
    <li>
      <svg viewBox="0 0 64 64" class="sym selected" aria-hidden="true">
        <circle cx="32" cy="32" r="26" class="outline" />
        <path d={PLANE} transform="translate(13 13) scale(0.6)" />
      </svg>
      Selected
    </li>
    <li><i class="line trail" aria-hidden="true"></i> Recent track</li>
    <li class="gap"><i class="dot" aria-hidden="true"></i> Home area</li>
    <li><i class="line ring" aria-hidden="true"></i> 1-mile rings</li>
    <li><i class="line visible" aria-hidden="true"></i> Visible area, {visibleLabel}</li>
    <li><i class="line collection" aria-hidden="true"></i> Collection limit</li>
    <li><span class="alt" aria-hidden="true">015</span> 1,500 ft (barometric)</li>
  </ul>
</details>

<style>
  .legend {
    position: absolute;
    bottom: 34px;
    left: 10px;
    z-index: 2;
    max-height: calc(100% - 90px);
    overflow-y: auto;
    border: 1px solid var(--control-line);
    border-radius: 4px;
    background: rgba(5, 12, 8, 0.88);
    color: var(--text-soft);
    font-size: 12px;
  }
  summary {
    padding: 5px 10px;
    color: var(--muted-strong);
    cursor: pointer;
    user-select: none;
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
