<script lang="ts">
  import { behaviorChip, identity, operatorTag } from '../lib/format';
  import type { Aircraft, Behavior } from '../lib/types';

  type ActivityKind = Exclude<Behavior['kind'], 'low'>;
  type ActivityBehavior = Behavior & { kind: ActivityKind };

  let { aircraft, onselect }: {
    aircraft: Aircraft[];
    onselect: (id: string, kind: ActivityKind) => void;
  } = $props();
  const headingId = $props.id();

  const entries = $derived(aircraft
    .filter((a) => a.freshness === 'fresh')
    .flatMap((a) => a.behaviors
      .filter((behavior): behavior is ActivityBehavior => behavior.kind !== 'low')
      .map((behavior) => ({ a, behavior }))));
</script>

{#if entries.length}
  <section class="activity" aria-labelledby={headingId} data-testid="activity">
    <h2 id={headingId}>Activity</h2>
    <ul>
      {#each entries as { a, behavior } (`${a.id}:${behavior.kind}`)}
        {@const operator = operatorTag(a)}
        <li>
          <button type="button" onclick={() => onselect(a.id, behavior.kind)}>
            <span class="identity">
              <strong>{identity(a)}</strong>
              {#if operator}<span class="operator role-{a.role}">{operator}</span>{/if}
            </span>
            <span class="behavior">{behaviorChip(behavior)}</span>
            <svg viewBox="0 0 16 16" aria-hidden="true">
              <path d="M6 2H2v4m8-4h4v4m0 4v4h-4m-4 0H2v-4" />
            </svg>
            <span class="visually-hidden">Frame on map</span>
          </button>
        </li>
      {/each}
    </ul>
  </section>
{/if}

<style>
  .activity {
    border-bottom: 1px solid var(--line);
    background: var(--panel);
  }
  h2 {
    margin: 0;
    padding: 8px 12px 4px;
    color: var(--muted-strong);
    font-size: 12px;
    font-weight: 600;
  }
  ul {
    margin: 0;
    padding: 0 0 4px;
    list-style: none;
  }
  button {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto 12px;
    align-items: center;
    gap: 8px;
    width: 100%;
    min-height: 32px;
    padding: 7px 12px;
    border: 0;
    background: none;
    color: var(--text);
    font: inherit;
    font-size: 13px;
    text-align: left;
    cursor: pointer;
  }
  button:hover,
  button:focus-visible {
    background: var(--hover);
  }
  button:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: -2px;
  }
  .identity {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  strong {
    font-family: var(--mono);
    font-weight: 500;
  }
  .operator {
    margin-left: 4px;
    font-size: 11px;
  }
  .behavior {
    color: var(--amber);
  }
  svg {
    width: 12px;
    height: 12px;
    fill: none;
    stroke: var(--muted-strong);
    stroke-width: 1.5;
  }
</style>
