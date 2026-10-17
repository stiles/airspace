<script lang="ts">
  import { altitude, cardinal, identity, miles } from '../lib/format';
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
            <strong>{identity(a)}</strong>
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
    border-bottom: 1px solid #1b2227;
    background: none;
    color: #e6edf2;
    font: inherit;
    font-size: 13px;
    text-align: left;
    cursor: pointer;
  }
  button:hover {
    background: #161d22;
  }
  button.selected {
    background: #12303f;
    box-shadow: inset 3px 0 #46c2ff;
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
    background: #e6edf2;
  }
  .kind.rotorcraft,
  .kind.gyroplane {
    background: #ffb020;
  }
  .kind.unknown {
    background: #9aa6af;
  }
  .kind.surface {
    border-radius: 1px;
    background: #6b7780;
  }
  .name {
    grid-area: name;
    display: flex;
    flex-direction: column;
    min-width: 0;
  }
  .type {
    overflow: hidden;
    color: #8d99a2;
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
    color: #8d99a2;
    font-size: 12px;
    text-align: right;
    font-variant-numeric: tabular-nums;
  }
  .empty {
    margin: 0;
    padding: 16px 12px;
    color: #aab5bc;
    font-size: 13px;
  }
</style>
