// Recolor a classic Mapbox style into a quiet radar scope. Works by layer type and
// id pattern, so it survives style revisions; Standard styles (imports) are left alone.

import type mapboxgl from 'mapbox-gl';
import { BASEMAP } from './theme';

// Clutter that doesn't help locate aircraft
const HIDE = [/^poi/, /transit/, /shield/, /^road-exit/, /^ferry/, /^aerialway/, /^natural-point/, /^water-point/];

const WATER = /water/;
const MAJOR_ROAD = /motorway|trunk|primary/;
const ROAD = /road|bridge|tunnel|street|path|rail/;
const BOUNDARY = /admin|boundary/;
const AEROWAY = /aeroway|airport|runway/;
const PLACE = /settlement|place|country|state/;

export function radarizeBasemap(map: mapboxgl.Map): boolean {
  const style = map.getStyle();
  if (!style || (style.imports && style.imports.length > 0)) return false;

  for (const layer of style.layers ?? []) {
    const id = layer.id;
    try {
      if (HIDE.some((re) => re.test(id))) {
        map.setLayoutProperty(id, 'visibility', 'none');
        continue;
      }
      switch (layer.type) {
        case 'background':
          map.setPaintProperty(id, 'background-color', BASEMAP.land);
          break;
        case 'fill':
          if (WATER.test(id)) map.setPaintProperty(id, 'fill-color', BASEMAP.water);
          else if (AEROWAY.test(id)) map.setPaintProperty(id, 'fill-color', BASEMAP.road);
          else if (/building/.test(id)) map.setPaintProperty(id, 'fill-color', BASEMAP.building);
          else map.setPaintProperty(id, 'fill-color', BASEMAP.landuse);
          break;
        case 'line':
          if (WATER.test(id)) map.setPaintProperty(id, 'line-color', BASEMAP.water);
          else if (BOUNDARY.test(id)) map.setPaintProperty(id, 'line-color', BASEMAP.boundary);
          else if (AEROWAY.test(id)) map.setPaintProperty(id, 'line-color', BASEMAP.roadMajor);
          else if (MAJOR_ROAD.test(id)) map.setPaintProperty(id, 'line-color', BASEMAP.roadMajor);
          else if (ROAD.test(id)) map.setPaintProperty(id, 'line-color', BASEMAP.road);
          break;
        case 'symbol':
          map.setPaintProperty(id, 'text-color', PLACE.test(id) ? BASEMAP.labelPlace : BASEMAP.label);
          map.setPaintProperty(id, 'text-halo-color', BASEMAP.halo);
          map.setPaintProperty(id, 'icon-opacity', 0.35);
          break;
      }
    } catch {
      // A layer without the property; leave it as styled
    }
  }
  return true;
}
