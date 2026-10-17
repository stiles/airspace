<script lang="ts">
  import type { StatusView } from '../lib/status';

  let { view }: { view: StatusView } = $props();
</script>

<div class="status {view.level}" role="status" aria-live="polite" data-testid="feed-status">
  <span class="dot" aria-hidden="true"></span>
  <span class="label">{view.label}</span>
  <span class="detail">{view.detail}</span>
</div>

<style>
  .status {
    display: flex;
    align-items: baseline;
    gap: 8px;
    min-width: 0;
    padding: 6px 12px;
    border-radius: 4px;
    font-size: 13px;
  }
  .dot {
    flex: none;
    width: 9px;
    height: 9px;
    border-radius: 50%;
    align-self: center;
  }
  .label {
    flex: none;
    font-weight: 500;
  }
  .detail {
    min-width: 0;
    overflow: hidden;
    color: #aab5bc;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .ok .dot {
    background: #3ccf7a;
  }
  .info .dot {
    background: #46c2ff;
  }
  .warn {
    background: rgba(255, 176, 32, 0.12);
  }
  .warn .dot {
    background: #ffb020;
  }
  /* Outages are loud on purpose */
  .alert {
    background: #5c1a1a;
    color: #fff;
  }
  .alert .dot {
    background: #ff5a5a;
    animation: pulse 1.2s ease-in-out infinite;
  }
  .alert .detail {
    color: #f3d0d0;
    white-space: normal;
  }
  @keyframes pulse {
    50% {
      opacity: 0.35;
    }
  }
</style>
