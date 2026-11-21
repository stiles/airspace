// Minimal stand-in for mapbox-gl that records what the component does
import { vi } from 'vitest';

export const instances: FakeMap[] = [];

export class FakeMap {
  sources = new Map<string, { spec: unknown; setData: ReturnType<typeof vi.fn> }>();
  layers: string[] = [];
  addSourceCalls = 0;
  addLayerCalls = 0;
  handlers = new Map<string, ((e: unknown) => void)[]>();
  canvas = { style: { cursor: '' } };
  touchZoomRotate = { disableRotation: vi.fn() };
  fitBounds = vi.fn();
  boundsContain = vi.fn(() => true);
  panTo = vi.fn();
  setFilter = vi.fn();
  addControl = vi.fn();
  remove = vi.fn();
  resize = vi.fn();
  images = new Set<string>();
  rendered: { properties: Record<string, unknown> }[] = [];

  constructor(public options: Record<string, unknown>) {
    instances.push(this);
    queueMicrotask(() => this.fire('load', {}));
  }
  on(event: string, layerOrHandler: unknown, handler?: (e: unknown) => void) {
    const key = typeof layerOrHandler === 'string' ? `${event}:${layerOrHandler}` : event;
    const fn = (handler ?? layerOrHandler) as (e: unknown) => void;
    this.handlers.set(key, [...(this.handlers.get(key) ?? []), fn]);
  }
  fire(key: string, e: unknown) {
    for (const fn of this.handlers.get(key) ?? []) fn(e);
  }
  addImage(name: string) {
    this.images.add(name);
  }
  hasImage(name: string) {
    return this.images.has(name);
  }
  addSource(id: string, spec: unknown) {
    this.addSourceCalls++;
    this.sources.set(id, { spec, setData: vi.fn() });
  }
  getSource(id: string) {
    return this.sources.get(id);
  }
  addLayer(layer: { id: string; layout?: Record<string, unknown> }) {
    this.addLayerCalls++;
    this.layers.push(layer.id);
    this.layerSpecs.push(layer);
  }
  layerSpecs: { id: string; layout?: Record<string, unknown> }[] = [];
  getStyle() {
    return { layers: [{ id: 'place-label', type: 'symbol', layout: { 'text-font': ['Inter Medium', 'Arial Unicode MS Regular'] } }] };
  }
  getCanvas() {
    return this.canvas;
  }
  getBounds() {
    return { contains: this.boundsContain };
  }
  // Fixed screen offset per degree, enough to check the hover card follows its aircraft
  projectScale = 1000;
  project([lon, lat]: [number, number]) {
    return { x: Math.round((lon + 118.5) * this.projectScale), y: Math.round((34.1 - lat) * this.projectScale) };
  }
  queryRenderedFeatures() {
    return this.rendered;
  }
}

export class NavigationControl {}
export class AttributionControl {}

export default { Map: FakeMap, NavigationControl, AttributionControl, accessToken: '' };
