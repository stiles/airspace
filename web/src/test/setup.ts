import '@testing-library/jest-dom/vitest';
import { vi } from 'vitest';

// Tests never use a real Mapbox token
vi.stubEnv('VITE_MAPBOX_TOKEN', 'pk.test-token');
