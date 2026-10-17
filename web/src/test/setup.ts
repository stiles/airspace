import '@testing-library/jest-dom/vitest';
import { vi } from 'vitest';

// Tests never use a real Mapbox token
vi.stubEnv('VITE_MAPBOX_TOKEN', 'pk.test-token');

// jsdom has no ResizeObserver (used by bind:clientWidth)
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver ??= ResizeObserverStub as unknown as typeof ResizeObserver;
