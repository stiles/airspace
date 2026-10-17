import { describe, expect, it, vi } from 'vitest';
import type mapboxgl from 'mapbox-gl';
import { radarizeBasemap } from './basemap';
import { BASEMAP } from './theme';

function fakeMap(layers: { id: string; type: string }[], imports: unknown[] = []) {
  const paint: Record<string, Record<string, unknown>> = {};
  const layout: Record<string, Record<string, unknown>> = {};
  const map = {
    getStyle: () => ({ layers, imports }),
    setPaintProperty: vi.fn((id: string, prop: string, value: unknown) => {
      if (id === 'broken') throw new Error('no such property');
      (paint[id] ??= {})[prop] = value;
    }),
    setLayoutProperty: vi.fn((id: string, prop: string, value: unknown) => ((layout[id] ??= {})[prop] = value)),
  };
  return { map: map as unknown as mapboxgl.Map, paint, layout };
}

describe('radarizeBasemap', () => {
  it('recolors by layer type and hides clutter', () => {
    const { map, paint, layout } = fakeMap([
      { id: 'land', type: 'background' },
      { id: 'water', type: 'fill' },
      { id: 'aeroway-polygon', type: 'fill' },
      { id: 'road-motorway-trunk', type: 'line' },
      { id: 'road-street', type: 'line' },
      { id: 'admin-1-boundary', type: 'line' },
      { id: 'settlement-major-label', type: 'symbol' },
      { id: 'poi-label', type: 'symbol' },
      { id: 'road-number-shield', type: 'symbol' },
      { id: 'broken', type: 'fill' },
    ]);
    expect(radarizeBasemap(map)).toBe(true);
    expect(paint.land['background-color']).toBe(BASEMAP.land);
    expect(paint.water['fill-color']).toBe(BASEMAP.water);
    expect(paint['aeroway-polygon']['fill-color']).toBe(BASEMAP.road);
    expect(paint['road-motorway-trunk']['line-color']).toBe(BASEMAP.roadMajor);
    expect(paint['road-street']['line-color']).toBe(BASEMAP.road);
    expect(paint['admin-1-boundary']['line-color']).toBe(BASEMAP.boundary);
    expect(paint['settlement-major-label']['text-color']).toBe(BASEMAP.labelPlace);
    expect(layout['poi-label'].visibility).toBe('none');
    expect(layout['road-number-shield'].visibility).toBe('none');
  });

  it('leaves Standard styles alone', () => {
    const { map } = fakeMap([{ id: 'land', type: 'background' }], [{ id: 'basemap' }]);
    expect(radarizeBasemap(map)).toBe(false);
    expect(map.setPaintProperty).not.toHaveBeenCalled();
  });
});
