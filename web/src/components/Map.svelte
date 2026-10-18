<script lang="ts">
  import mapboxgl, { type GeoJSONSource } from 'mapbox-gl';
  import 'mapbox-gl/dist/mapbox-gl.css';
  import { onDestroy, onMount, untrack } from 'svelte';
  import { aircraftCollection, circle, rings, trailCollection } from '../lib/geo';
  import { drawIcon, ICON_NAMES, ICON_PIXEL_RATIO } from '../lib/icons';
  import { radarizeBasemap } from '../lib/basemap';
  import { FALLBACK_FONT, MAPBOX_STYLE, MAPBOX_TOKEN } from '../lib/config';
  import { SCOPE } from '../lib/theme';
  import { miles } from '../lib/format';
  import { tooltipFor } from '../lib/tooltip';
  import Legend from './Legend.svelte';
  import type { Aircraft, LonLat, Snapshot } from '../lib/types';

  interface Props {
    aircraft: Aircraft[];
    area: Snapshot['area'] | null;
    selectedId: string | null;
    onselect: (id: string | null) => void;
    /** Compact mode: no detail panel, so the selection keeps its hover card open */
    compact?: boolean;
  }

  let { aircraft, area, selectedId, onselect, compact = false }: Props = $props();

  const TOKEN = MAPBOX_TOKEN;

  const kindColor: mapboxgl.ExpressionSpecification = [
    'match',
    ['get', 'kind'],
    ['rotorcraft', 'gyroplane'],
    SCOPE.amber,
    'surface',
    SCOPE.surface,
    'unknown',
    SCOPE.unknown,
    SCOPE.phosphor,
  ];
  const freshnessOpacity: mapboxgl.ExpressionSpecification = ['match', ['get', 'freshness'], 'fading', 0.4, 'unknown', 0.75, 1];

  let container = $state<HTMLDivElement>();
  let map: mapboxgl.Map | null = null;
  let ready = $state(false);
  let fittedHome: string | null = null;
  let hoveredId = $state<string | null>(null);
  // Bumped on every camera move so the hover card follows its aircraft
  let view = $state(0);
  let width = $state(0);
  let height = $state(0);

  const tipTarget = $derived(hoveredId ?? (compact ? selectedId : null));
  const tipAircraft = $derived(tipTarget ? (aircraft.find((a) => a.id === tipTarget) ?? null) : null);
  const tip = $derived(tipAircraft ? tooltipFor(tipAircraft) : null);
  const tipPoint = $derived.by(() => {
    void view;
    if (!ready || !map || !tipAircraft) return null;
    return map.project([tipAircraft.longitude, tipAircraft.latitude]);
  });
  // Keep the card on screen: flip left near the right edge, clamp near top and bottom
  const tipLeft = $derived(tipPoint !== null && tipPoint.x > width - 230);
  const tipY = $derived(tipPoint ? Math.min(Math.max(tipPoint.y, 44), Math.max(44, height - 44)) : 0);
  const visibleLabel = $derived(area ? miles(area.visible_radius_m) : '');

  function source(id: string): GeoJSONSource | undefined {
    return map?.getSource(id) as GeoJSONSource | undefined;
  }

  function visibleBounds(a: Snapshot['area']): mapboxgl.LngLatBoundsLike {
    const ring = circle([a.home.longitude, a.home.latitude], a.visible_radius_m * 1.08, 32);
    const lons = ring.map((p) => p[0]);
    const lats = ring.map((p) => p[1]);
    return [
      [Math.min(...lons), Math.min(...lats)],
      [Math.max(...lons), Math.max(...lats)],
    ];
  }

  function recenter() {
    if (map && area) map.fitBounds(visibleBounds(area), { padding: 24, duration: 600 });
  }

  /** Borrow a font stack from the style's own labels, so glyph requests always resolve. */
  function styleFont(m: mapboxgl.Map): string[] {
    for (const layer of m.getStyle()?.layers ?? []) {
      const font = layer.type === 'symbol' ? layer.layout?.['text-font'] : undefined;
      if (Array.isArray(font) && font.every((f) => typeof f === 'string')) return font as string[];
    }
    return FALLBACK_FONT;
  }

  // Mapbox Standard styles light the map; emissive strength 1 keeps overlays at their true colors
  // under night presets (classic styles ignore it)
  /** Afterglow: transparent at the oldest point, bright at the aircraft. Needs lineMetrics. */
  function glow(hex: string, peak: number): mapboxgl.ExpressionSpecification {
    const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
    return ['interpolate', ['linear'], ['line-progress'], 0, `rgba(${r},${g},${b},0)`, 1, `rgba(${r},${g},${b},${peak})`];
  }

  function addLayers(m: mapboxgl.Map) {
    radarizeBasemap(m);
    const FONT = styleFont(m);
    for (const name of ICON_NAMES) {
      const image = drawIcon(name);
      if (image && !m.hasImage(name)) m.addImage(name, image, { sdf: true, pixelRatio: ICON_PIXEL_RATIO });
    }
    const empty = { type: 'FeatureCollection' as const, features: [] };
    m.addSource('rings', { type: 'geojson', data: empty });
    m.addSource('trails', { type: 'geojson', data: empty, lineMetrics: true });
    m.addSource('aircraft', { type: 'geojson', data: empty, promoteId: 'id' });

    m.addLayer({
      id: 'rings',
      type: 'line',
      source: 'rings',
      filter: ['all', ['==', ['geometry-type'], 'LineString'], ['!=', ['get', 'kind'], 'collection']],
      paint: {
        'line-color': ['match', ['get', 'kind'], 'visible', SCOPE.phosphor, SCOPE.ring],
        'line-width': ['match', ['get', 'kind'], 'visible', 1.25, 1],
        'line-opacity': ['match', ['get', 'kind'], 'visible', 0.55, 0.7],
        'line-emissive-strength': 1,
      },
    });
    m.addLayer({
      id: 'collection-ring',
      type: 'line',
      source: 'rings',
      filter: ['all', ['==', ['geometry-type'], 'LineString'], ['==', ['get', 'kind'], 'collection']],
      paint: {
        'line-color': SCOPE.ring,
        'line-width': 1,
        'line-dasharray': [2, 3],
        'line-opacity': 0.6,
        'line-emissive-strength': 1,
      },
    });
    m.addLayer({
      id: 'ring-labels',
      type: 'symbol',
      source: 'rings',
      filter: ['all', ['==', ['geometry-type'], 'Point'], ['!=', ['get', 'kind'], 'home']],
      layout: { 'text-field': ['get', 'label'], 'text-font': FONT, 'text-size': 11, 'text-offset': [0, -0.7] },
      paint: {
        'text-color': SCOPE.ringLabel,
        'text-halo-color': SCOPE.halo,
        'text-halo-width': 1,
        'text-emissive-strength': 1,
      },
    });
    m.addLayer({
      id: 'home',
      type: 'circle',
      source: 'rings',
      filter: ['==', ['get', 'kind'], 'home'],
      paint: {
        'circle-radius': 4,
        'circle-color': SCOPE.accent,
        'circle-stroke-color': SCOPE.halo,
        'circle-stroke-width': 2,
        'circle-emissive-strength': 1,
      },
    });
    // line-gradient can't vary by feature, so each trail color gets its own layer
    const helicopter: mapboxgl.ExpressionSpecification = ['match', ['get', 'kind'], ['rotorcraft', 'gyroplane'], true, false];
    const trailLayers: [string, mapboxgl.ExpressionSpecification, string, number, number][] = [
      ['trails', ['all', ['!', ['get', 'selected']], ['!', helicopter]], SCOPE.phosphor, 0.7, 1.5],
      ['trails-heli', ['all', ['!', ['get', 'selected']], helicopter], SCOPE.amber, 0.8, 1.75],
      ['trails-selected', ['get', 'selected'], SCOPE.accent, 1, 2.5],
    ];
    for (const [id, filter, color, peak, width] of trailLayers) {
      m.addLayer({
        id,
        type: 'line',
        source: 'trails',
        filter,
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: {
          'line-gradient': glow(color, peak),
          'line-width': width,
          'line-opacity': freshnessOpacity,
          'line-emissive-strength': 1,
        },
      });
    }
    m.addLayer({
      id: 'selection',
      type: 'circle',
      source: 'aircraft',
      filter: ['==', ['get', 'selected'], true],
      paint: {
        'circle-radius': 17,
        'circle-color': 'transparent',
        'circle-stroke-color': SCOPE.accent,
        'circle-stroke-width': 2,
        'circle-emissive-strength': 1,
      },
    });
    m.addLayer({
      id: 'aircraft',
      type: 'symbol',
      source: 'aircraft',
      layout: {
        'icon-image': ['get', 'icon'],
        'icon-size': ['match', ['get', 'kind'], ['rotorcraft', 'gyroplane'], 0.85, 'surface', 0.5, 0.75],
        // Track is relative to true north, so rotate with the map
        'icon-rotate': ['get', 'rotation'],
        'icon-rotation-alignment': 'map',
        'icon-allow-overlap': true,
        'icon-ignore-placement': true,
        'symbol-sort-key': ['case', ['get', 'selected'], 2, ['match', ['get', 'kind'], ['rotorcraft', 'gyroplane'], 1, 0]],
        'text-field': ['get', 'label'],
        'text-font': FONT,
        'text-size': 11,
        'text-anchor': 'top',
        'text-offset': [0, 1.3],
        'text-optional': true,
      },
      paint: {
        'icon-color': ['case', ['get', 'selected'], SCOPE.accent, kindColor],
        'icon-opacity': freshnessOpacity,
        'text-color': ['case', ['get', 'selected'], SCOPE.accent, helicopter, SCOPE.amber, SCOPE.phosphorText],
        'text-opacity': freshnessOpacity,
        'text-halo-color': SCOPE.halo,
        'text-halo-width': 1.2,
        'icon-emissive-strength': 1,
        'text-emissive-strength': 1,
      },
    });

    m.on('click', (e) => {
      const hit = m.queryRenderedFeatures(e.point, { layers: ['aircraft'] })[0];
      onselect(hit ? String(hit.properties?.id) : null);
    });
    m.on('mousemove', 'aircraft', (e) => {
      const id = e.features?.[0]?.properties?.id;
      hoveredId = id === undefined ? null : String(id);
      m.getCanvas().style.cursor = 'pointer';
    });
    m.on('mouseleave', 'aircraft', () => {
      hoveredId = null;
      m.getCanvas().style.cursor = '';
    });
    m.on('move', () => view++);
  }

  onMount(() => {
    if (!TOKEN) return;
    mapboxgl.accessToken = TOKEN;
    const home: LonLat = area ? [area.home.longitude, area.home.latitude] : [-118.42, 34.0];
    map = new mapboxgl.Map({
      container: container!,
      style: MAPBOX_STYLE,
      center: home,
      zoom: 11,
      projection: 'mercator',
      pitchWithRotate: false,
      dragRotate: false, // north-up
      touchPitch: false,
      attributionControl: true,
    });
    map.touchZoomRotate.disableRotation();
    map.addControl(new mapboxgl.NavigationControl({ showCompass: false }), 'top-right');
    map.on('load', () => {
      addLayers(map!);
      ready = true;
    });
  });

  onDestroy(() => map?.remove());

  // Area changes rarely: rings, and the initial view
  $effect(() => {
    if (!ready || !area || !map) return;
    const home: LonLat = [area.home.longitude, area.home.latitude];
    source('rings')?.setData(rings(home, area.visible_radius_m, area.collection_radius_m));
    const key = home.join(',');
    if (fittedHome !== key) {
      fittedHome = key;
      map.fitBounds(visibleBounds(area), { padding: 24, duration: 0 });
    }
  });

  // Layout changes (compact mode, the replay bar) resize the container; keep the canvas in step
  $effect(() => {
    if (!ready || !width || !height) return;
    map?.resize();
  });

  // Switching layouts changes the map size a lot; refit so the visible area stays in view
  let fittedCompact = untrack(() => compact);
  $effect(() => {
    const c = compact;
    if (!ready || c === fittedCompact) return;
    fittedCompact = c;
    requestAnimationFrame(() => {
      map?.resize();
      if (map && area) map.fitBounds(visibleBounds(area), { padding: 24, duration: 0 });
    });
  });

  // Every update: replace source data in place, never rebuild layers
  $effect(() => {
    if (!ready) return;
    source('aircraft')?.setData(aircraftCollection(aircraft, selectedId));
    source('trails')?.setData(trailCollection(aircraft, selectedId));
  });
</script>

<div class="map-wrap" bind:clientWidth={width} bind:clientHeight={height}>
  {#if TOKEN}
    <div class="map" bind:this={container} data-testid="map"></div>
    <button class="recenter" type="button" onclick={recenter} aria-label="Recenter on home area">Recenter</button>
    {#if area}
      <!-- Remount on layout change so each layout keeps its own open/closed choice -->
      {#key compact}
        <Legend {visibleLabel} {compact} />
      {/key}
    {/if}
    {#if tip && tipPoint}
      <div
        class="tooltip"
        class:left={tipLeft}
        class:heli={tipAircraft?.class === 'rotorcraft' || tipAircraft?.class === 'gyroplane'}
        style:left="{tipPoint.x}px"
        style:top="{tipY}px"
        role="tooltip"
        data-testid="map-tooltip"
      >
        <strong>{tip.title}</strong>
        <span class="sub">{tip.subtitle}</span>
        {#each tip.lines as line (line)}
          <span>{line}</span>
        {/each}
        {#if tip.note}
          <span class="note">{tip.note}</span>
        {/if}
      </div>
    {/if}
  {:else}
    <div class="no-token">
      <p>Map unavailable: set <code>VITE_MAPBOX_TOKEN</code> in <code>web/.env.local</code> and restart.</p>
    </div>
  {/if}
</div>

<style>
  .map-wrap {
    position: relative;
    width: 100%;
    height: 100%;
    background: var(--bg);
  }
  .map {
    position: absolute;
    inset: 0;
  }
  .recenter {
    position: absolute;
    top: 10px;
    left: 10px;
    padding: 6px 10px;
    border: 1px solid var(--control-line);
    border-radius: 4px;
    background: rgba(17, 22, 26, 0.9);
    color: var(--text);
    font: inherit;
    font-size: 13px;
    cursor: pointer;
  }
  .recenter:hover {
    border-color: var(--control-line-hover);
  }
  .tooltip {
    position: absolute;
    z-index: 3;
    display: flex;
    flex-direction: column;
    gap: 1px;
    max-width: 210px;
    padding: 6px 9px;
    border: 1px solid var(--control-line);
    border-left: 2px solid var(--phosphor);
    border-radius: 3px;
    background: rgba(5, 12, 8, 0.94);
    color: var(--text-soft);
    font-size: 12px;
    font-variant-numeric: tabular-nums;
    pointer-events: none;
    transform: translate(16px, -50%);
  }
  .tooltip.left {
    transform: translate(calc(-100% - 16px), -50%);
  }
  .tooltip.heli {
    border-left-color: var(--amber);
  }
  .tooltip strong {
    color: var(--text);
    font-family: var(--mono);
    font-weight: 500;
    letter-spacing: 0.02em;
  }
  .tooltip .sub {
    overflow: hidden;
    color: var(--muted-strong);
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .tooltip .note {
    color: var(--amber);
  }
  .no-token {
    display: grid;
    place-items: center;
    height: 100%;
    padding: 24px;
    color: var(--text-soft);
    text-align: center;
  }
</style>
