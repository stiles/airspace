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
    color: var(--muted-strong);
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .ok .dot {
    background: var(--phosphor);
    box-shadow: 0 0 0 0 color-mix(in srgb, var(--phosphor) 55%, transparent);
  }
  .info .dot {
    background: var(--accent);
  }
  .warn {
    background: rgba(255, 176, 32, 0.12);
  }
  .warn .dot {
    background: var(--amber);
  }
  /* Outages are loud on purpose */
  .alert {
    background: var(--alert-bg);
    color: #fff;
  }
  .alert .dot {
    background: var(--alert);
  }
  .alert .detail {
    color: var(--alert-text);
    white-space: normal;
  }
  /* Soft status LEDs; opacity only, under 3 flashes/s, off when reduced motion is preferred */
  @media (prefers-reduced-motion: no-preference) {
    .ok .dot {
      animation: live-pulse 2.4s ease-in-out infinite;
    }
    .alert .dot {
      animation: alert-pulse 1.2s ease-in-out infinite;
    }
  }
  @keyframes live-pulse {
    0%,
    100% {
      box-shadow: 0 0 0 0 color-mix(in srgb, var(--phosphor) 50%, transparent);
      opacity: 1;
    }
    50% {
      box-shadow: 0 0 0 6px transparent;
      opacity: 0.55;
    }
  }
  @keyframes alert-pulse {
    50% {
      opacity: 0.35;
    }
  }
</style>
