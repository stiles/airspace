<script lang="ts">
  import { ALTITUDE_PRESETS, type Filters } from '../lib/filters';

  let { filters = $bindable(), visibleLabel }: { filters: Filters; visibleLabel: string } = $props();
</script>

<fieldset class="filters">
  <legend class="visually-hidden">Filters</legend>
  <label class="select">
    <span class="visually-hidden">Altitude</span>
    <select bind:value={filters.maxAltitudeFt} data-testid="altitude-filter">
      {#each ALTITUDE_PRESETS as preset (preset.label)}
        <option value={preset.value}>{preset.label} (barometric)</option>
      {/each}
    </select>
  </label>
  <label><input type="checkbox" bind:checked={filters.includeUnknownAltitude} /> Unknown altitude</label>
  <label><input type="checkbox" bind:checked={filters.includeOnGround} /> On the ground</label>
  <label><input type="checkbox" bind:checked={filters.includeSurface} /> Surface vehicles</label>
  <label><input type="checkbox" bind:checked={filters.listVisibleOnly} /> List only within {visibleLabel}</label>
</fieldset>

<style>
  .filters {
    display: flex;
    flex-wrap: wrap;
    gap: 6px 14px;
    margin: 0;
    padding: 10px 12px;
    border: 0;
    border-bottom: 1px solid #222b31;
    font-size: 13px;
    color: #c3ccd2;
  }
  .select {
    flex-basis: 100%;
  }
  select {
    width: 100%;
    padding: 5px 6px;
    border: 1px solid #36424a;
    border-radius: 4px;
    background: #161c20;
    color: #e6edf2;
    font: inherit;
  }
  label {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    cursor: pointer;
  }
  input {
    accent-color: #46c2ff;
  }
</style>
