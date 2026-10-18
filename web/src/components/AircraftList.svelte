<script lang="ts">
  import { altitude, behaviorChip, cardinal, identity, miles, operatorTag } from '../lib/format';
  import type { Aircraft } from '../lib/types';

  interface Props {
    aircraft: Aircraft[];
    selectedId: string | null;
    emptyMessage: string;
    onselect: (id: string) => void;
  }

  let { aircraft, selectedId, emptyMessage, onselect }: Props = $props();
</script>

{#if aircraft.length === 0}
  <p class="empty" data-testid="list-empty">{emptyMessage}</p>
{:else}
  <ul class="list" aria-label="Aircraft by distance" data-testid="aircraft-list">
    {#each aircraft as a (a.id)}
      <li>
        <button
          type="button"
          class:selected={a.id === selectedId}
          class:fading={a.freshness === 'fading'}
          aria-pressed={a.id === selectedId}
          onclick={() => onselect(a.id)}
          data-id={a.id}
        >
          <span class="kind {a.class}" aria-hidden="true"></span>
          <span class="name">
            <span class="id">
              <strong>{identity(a)}</strong>
              {#if operatorTag(a)}
                <span class="tag role-{a.role}" title={a.operator ?? undefined}>{operatorTag(a)}</span>
              {/if}
              {#each a.behaviors as b (b.kind)}
                <span class="behavior" data-testid="behavior-chip">{behaviorChip(b)}</span>
              {/each}
            </span>
            <span class="type">{a.description ?? a.aircraft_type ?? 'Type unknown'}</span>
          </span>
          <span class="alt">{altitude(a)}</span>
          <span class="dist">{miles(a.distance_m)} {cardinal(a.bearing_deg)}</span>
        </button>
      </li>
    {/each}
  </ul>
{/if}

<style>
  .list {
    margin: 0;
    padding: 0;
    list-style: none;
  }
  button {
    display: grid;
    grid-template-columns: 10px 1fr auto;
    grid-template-areas: 'kind name alt' 'kind name dist';
    gap: 0 10px;
    width: 100%;
    padding: 8px 12px;
    border: 0;
    border-bottom: 1px solid var(--line-soft);
    background: none;
    color: var(--text);
    font: inherit;
    font-size: 13px;
    text-align: left;
    cursor: pointer;
  }
  button:hover {
    background: var(--hover);
  }
  button.selected {
    background: var(--selected-bg);
    box-shadow: inset 3px 0 var(--accent);
  }
  button.fading {
    opacity: 0.55;
  }
  .kind {
    grid-area: kind;
    align-self: center;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--phosphor);
  }
  .kind.rotorcraft,
  .kind.gyroplane {
    background: var(--amber);
  }
  .kind.unknown {
    background: var(--unknown);
  }
  .kind.surface {
    border-radius: 1px;
    background: var(--surface);
  }
  .name strong {
    font-family: var(--mono);
    font-weight: 500;
    letter-spacing: 0.02em;
  }
  .id {
    display: flex;
    align-items: center;
    gap: 6px;
    min-width: 0;
  }
  .tag {
    flex: none;
    padding: 0 4px;
    border: 1px solid currentColor;
    border-radius: 3px;
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.04em;
    line-height: 14px;
    text-transform: uppercase;
  }
  .behavior {
    flex: none;
    color: var(--amber);
    font-size: 11px;
    white-space: nowrap;
  }
  .behavior::before {
    content: '◌ ';
  }
  .name {
    grid-area: name;
    display: flex;
    flex-direction: column;
    min-width: 0;
  }
  .type {
    overflow: hidden;
    color: var(--muted);
    font-size: 12px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .alt {
    grid-area: alt;
    text-align: right;
    font-variant-numeric: tabular-nums;
  }
  .dist {
    grid-area: dist;
    color: var(--muted);
    font-size: 12px;
    text-align: right;
    font-variant-numeric: tabular-nums;
  }
  .empty {
    margin: 0;
    padding: 16px 12px;
    color: var(--muted-strong);
    font-size: 13px;
  }
</style>
