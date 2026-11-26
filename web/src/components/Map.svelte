<script lang="ts">
  import mapboxgl, { type GeoJSONSource } from 'mapbox-gl';
  import 'mapbox-gl/dist/mapbox-gl.css';
  import { onDestroy, onMount, untrack } from 'svelte';
  import { aircraftCollection, behaviorCollection, circleBounds, destination, rings, trailCollection } from '../lib/geo';
  import { drawIcon, ICON_NAMES, ICON_PIXEL_RATIO } from '../lib/icons';
  import { radarizeBasemap } from '../lib/basemap';
  import { FALLBACK_FONT, MAPBOX_STYLE, MAPBOX_TOKEN } from '../lib/config';
  import { SCOPE } from '../lib/theme';
  import { METERS_PER_MILE, miles } from '../lib/format';
  import { tooltipFor } from '../lib/tooltip';
  import Legend from './Legend.svelte';
  import type { Aircraft, LonLat, Outlines, Snapshot } from '../lib/types';

  interface Props {
    aircraft: Aircraft[];
    area: Snapshot['area'] | null;
    selectedId: string | null;
    highlightedId: string | null;
    listSelection: { id: string; behavior?: 'circling' | 'hovering' } | null;
    onselect: (id: string | null) => void;
    onhighlight: (id: string | null) => void;
    /** Compact mode: no detail panel, so the selection keeps its hover card open */
    compact?: boolean;
    outlines?: Outlines | null;
    outlineNames?: string[];
    readOnly?: boolean;
  }

  let {
    aircraft, area, selectedId, highlightedId, listSelection, onselect, onhighlight,
    compact = false, outlines = null, outlineNames = [], readOnly = false,
  }: Props = $props();

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
  const ringLabels: mapboxgl.ExpressionSpecification = ['all', ['==', ['geometry-type'], 'Point'], ['!=', ['get', 'kind'], 'home']];
  const majorRingLabels: mapboxgl.ExpressionSpecification = ['all', ringLabels, ['==', ['get', 'major'], true]];

  let container = $state<HTMLDivElement>();
  let map: mapboxgl.Map | null = null;
  let ready = $state(false);
  let homePulse = 0;
  let fittedHome: string | null = null;
  let sparseRingLabels = true;
  // Set once someone pans or zooms; until then, size changes (a kiosk window going full screen) refit
  let userMoved = false;
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

  function viewBounds(a: Snapshot['area']): mapboxgl.LngLatBoundsLike {
    const radius = a.view_radius_m ?? a.visible_radius_m * 1.08;
    return circleBounds([a.home.longitude, a.home.latitude], radius);
  }

  function updateRingLabels(m: mapboxgl.Map, home: LonLat) {
    const origin = m.project(home);
    const nextMile = m.project(destination(home, 0, METERS_PER_MILE));
    const sparse = Math.abs(origin.y - nextMile.y) < 56;
    if (sparse === sparseRingLabels) return;
    sparseRingLabels = sparse;
    m.setFilter('ring-labels', sparse ? majorRingLabels : ringLabels);
  }

  function setHoveredAircraft(id: string | null) {
    if (hoveredId === id) return;
    hoveredId = id;
    onhighlight(id);
    const canvas = map?.getCanvas();
    if (canvas) canvas.style.cursor = id === null ? '' : 'pointer';
  }

  function recenter() {
    userMoved = false;
    if (map && area) map.fitBounds(viewBounds(area), { padding: 24, duration: 600 });
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
    m.addSource('outlines', { type: 'geojson', data: empty });
    m.addSource('rings', { type: 'geojson', data: empty });
    m.addSource('trails', { type: 'geojson', data: empty, lineMetrics: true });
    m.addSource('behaviors', { type: 'geojson', data: empty });
    m.addSource('aircraft', { type: 'geojson', data: empty, promoteId: 'id' });
    m.addSource('69-bravo', {
      type: 'geojson',
      data: {
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [-118.6273692, 34.074326] },
        properties: { name: '69 Bravo' },
      },
    });

    // A soft glow under a solid line, so it reads against the basemap's own boundary lines
    m.addLayer({
      id: 'outlines-glow',
      type: 'line',
      source: 'outlines',
      layout: { 'line-join': 'round' },
      paint: {
        'line-color': SCOPE.outline,
        'line-width': 7,
        'line-blur': 5,
        'line-opacity': 0.3,
        'line-emissive-strength': 1,
      },
    });
    m.addLayer({
      id: 'outlines',
      type: 'line',
      source: 'outlines',
      layout: { 'line-join': 'round' },
      paint: {
        'line-color': SCOPE.outline,
        'line-width': 2,
        'line-opacity': 0.9,
        'line-emissive-strength': 1,
      },
    });
    m.addLayer({
      id: 'rings',
      type: 'line',
      source: 'rings',
      filter: ['all', ['==', ['geometry-type'], 'LineString'], ['!=', ['get', 'kind'], 'collection']],
      paint: {
        'line-color': ['match', ['get', 'kind'], 'visible', SCOPE.phosphor, SCOPE.ring],
        'line-width': ['match', ['get', 'kind'], 'visible', 1.25, 0.75],
        'line-opacity': ['match', ['get', 'kind'], 'visible', 0.55, 0.45],
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
      filter: majorRingLabels,
      layout: {
        'text-field': ['get', 'label'],
        'text-font': FONT,
        'text-size': 11,
        'text-offset': [0, -0.7],
        'symbol-sort-key': ['match', ['get', 'kind'], 'collection', 0, 'visible', 1, 2],
      },
      paint: {
        'text-color': SCOPE.ringLabel,
        'text-opacity': ['match', ['get', 'kind'], 'ring', 0.75, 1],
        'text-halo-color': SCOPE.halo,
        'text-halo-width': 1,
        'text-emissive-strength': 1,
      },
    });
    m.addLayer({
      id: 'home-pulse',
      type: 'circle',
      source: 'rings',
      filter: ['==', ['get', 'kind'], 'home'],
      paint: {
        'circle-radius': 4,
        'circle-color': SCOPE.accent,
        'circle-opacity': 0,
        'circle-emissive-strength': 1,
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
    // Inferred orbits and hovers: a dashed ring, under trails and symbols
    m.addLayer({
      id: 'behaviors',
      type: 'line',
      source: 'behaviors',
      paint: {
        'line-color': ['case', ['get', 'selected'], SCOPE.accent, SCOPE.amber],
        'line-width': 1.25,
        'line-dasharray': [1.5, 2],
        'line-opacity': 0.75,
        'line-emissive-strength': 1,
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
          'line-width': id === 'trails-selected'
            ? width
            : ['case', ['get', 'highlighted'], width + 0.9, ['get', 'insideVisible'], width, width * 0.55],
          'line-opacity': freshnessOpacity,
          'line-emissive-strength': 1,
        },
      });
    }
    m.addLayer({
      id: '69-bravo-marker',
      type: 'circle',
      source: '69-bravo',
      paint: {
        'circle-radius': 3,
        'circle-color': SCOPE.halo,
        'circle-stroke-color': SCOPE.landmark,
        'circle-stroke-width': 1.5,
        'circle-emissive-strength': 1,
      },
    });
    m.addLayer({
      id: '69-bravo-label',
      type: 'symbol',
      source: '69-bravo',
      layout: {
        'text-field': ['get', 'name'],
        'text-font': FONT,
        'text-size': 12,
        'text-variable-anchor': ['right', 'top', 'bottom', 'left'],
        'text-radial-offset': 0.65,
        'text-ignore-placement': true,
      },
      paint: {
        'text-color': SCOPE.landmark,
        'text-halo-color': SCOPE.halo,
        'text-halo-width': 1.5,
        'text-emissive-strength': 1,
      },
    });
    m.addLayer({
      id: 'selection',
      type: 'circle',
      source: 'aircraft',
      filter: ['any', ['get', 'selected'], ['get', 'highlighted']],
      paint: {
        'circle-radius': ['case', ['get', 'selected'], 17, 15],
        'circle-color': 'transparent',
        'circle-stroke-color': ['case', ['get', 'selected'], SCOPE.accent, SCOPE.phosphorText],
        'circle-stroke-opacity': ['case', ['get', 'selected'], 1, freshnessOpacity],
        'circle-stroke-width': ['case', ['get', 'selected'], 2, 1.5],
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
        'symbol-sort-key': ['case', ['get', 'selected'], 3, ['get', 'highlighted'], 2, helicopter, 1, 0],
        'text-field': ['get', 'label'],
        'text-font': FONT,
        'text-size': 12,
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
        'text-halo-width': 1.7,
        'text-halo-blur': 0.3,
        'icon-emissive-strength': 1,
        'text-emissive-strength': 1,
      },
    });

    m.on('zoomend', () => {
      const a = untrack(() => area);
      if (a) updateRingLabels(m, [a.home.longitude, a.home.latitude]);
    });
    if (readOnly) return;

    m.on('click', (e) => {
      const hit = m.queryRenderedFeatures(e.point, { layers: ['aircraft'] })[0];
      onselect(hit ? String(hit.properties?.id) : null);
    });
    m.on('mousemove', 'aircraft', (e) => {
      const id = e.features?.[0]?.properties?.id;
      setHoveredAircraft(id === undefined ? null : String(id));
    });
    m.on('mouseleave', 'aircraft', () => setHoveredAircraft(null));
    m.on('move', () => view++);
    // Camera moves from fitBounds carry no originalEvent; drags, scrolls and the zoom buttons do
    m.on('movestart', (e) => {
      if ((e as { originalEvent?: unknown }).originalEvent) userMoved = true;
    });
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
      interactive: !readOnly,
      attributionControl: !readOnly,
    });
    if (readOnly) {
      map.addControl(new mapboxgl.AttributionControl({ compact: false }));
    } else {
      map.touchZoomRotate.disableRotation();
      map.addControl(new mapboxgl.NavigationControl({ showCompass: false }), 'top-right');
    }
    map.on('load', () => {
      addLayers(map!);
      startHomePulse(map!);
      ready = true;
    });
  });

  function startHomePulse(m: mapboxgl.Map) {
    // jsdom has no matchMedia; treat that as reduced motion (no animation in tests)
    if (typeof matchMedia !== 'function' || !matchMedia('(prefers-reduced-motion: no-preference)').matches) return;
    const periodMs = 2400;
    const tick = (t: number) => {
      if (!map || map !== m || !m.getLayer('home-pulse')) return;
      const phase = (t % periodMs) / periodMs;
      m.setPaintProperty('home-pulse', 'circle-radius', 4 + phase * 16);
      m.setPaintProperty('home-pulse', 'circle-opacity', 0.5 * (1 - phase));
      homePulse = requestAnimationFrame(tick);
    };
    homePulse = requestAnimationFrame(tick);
  }

  onDestroy(() => {
    cancelAnimationFrame(homePulse);
    map?.remove();
  });

  // Area changes rarely: rings, and the initial view
  $effect(() => {
    if (!ready || !area || !map) return;
    const home: LonLat = [area.home.longitude, area.home.latitude];
    source('rings')?.setData(rings(home, area.visible_radius_m, area.collection_radius_m));
    const key = `${home.join(',')},${area.view_radius_m ?? ''}`;
    if (fittedHome !== key) {
      fittedHome = key;
      map.fitBounds(viewBounds(area), { padding: 24, duration: 0 });
    }
    updateRingLabels(map, home);
  });

  $effect(() => {
    if (ready) source('outlines')?.setData(outlines ?? { type: 'FeatureCollection', features: [] });
  });

  // Layout changes (compact mode, the replay bar, a window going full screen) resize the container;
  // keep the canvas in step, and keep the home view framed until someone moves the map
  $effect(() => {
    if (!ready || !width || !height) return;
    map?.resize();
    const a = untrack(() => area);
    if (!userMoved && map && a) map.fitBounds(viewBounds(a), { padding: 24, duration: 0 });
  });

  // Switching layouts changes the map size a lot; refit so the visible area stays in view
  let fittedCompact = untrack(() => compact);
  $effect(() => {
    const c = compact;
    if (!ready || c === fittedCompact) return;
    fittedCompact = c;
    requestAnimationFrame(() => {
      map?.resize();
      if (map && area) map.fitBounds(viewBounds(area), { padding: 24, duration: 0 });
    });
  });

  // Frame a behavior or pan once to a row. Polls and replay seeks only update the marker.
  $effect(() => {
    const m = map;
    if (!ready || !m || !listSelection) return;
    const { id, behavior } = listSelection;
    untrack(() => {
      const target = aircraft.find((a) => a.id === id);
      if (!target) return;
      const episode = behavior && target.behaviors.find((b) => b.kind === behavior);
      if (episode) {
        userMoved = true;
        m.fitBounds(circleBounds(episode.center, Math.max(episode.radius_m * 1.4, 400)), {
          padding: 40, duration: 600, maxZoom: 16,
        });
        return;
      }
      const position: LonLat = [target.longitude, target.latitude];
      const bounds = m.getBounds();
      // The same aircraft can be rendered on the next world copy across the antimeridian.
      if (bounds.contains(position) ||
          bounds.contains([position[0] - 360, position[1]]) ||
          bounds.contains([position[0] + 360, position[1]])) return;
      userMoved = true;
      m.panTo(position, { duration: 450 });
    });
  });

  $effect(() => {
    if (hoveredId !== null && !aircraft.some((a) => a.id === hoveredId)) {
      untrack(() => setHoveredAircraft(null));
    }
  });

  // Every update: replace source data in place, never rebuild layers
  $effect(() => {
    if (!ready) return;
    source('aircraft')?.setData(aircraftCollection(aircraft, selectedId, highlightedId));
    source('trails')?.setData(trailCollection(aircraft, selectedId, highlightedId));
  });

  $effect(() => {
    if (!ready) return;
    source('behaviors')?.setData(behaviorCollection(aircraft, selectedId));
  });
</script>

<div class="map-wrap" bind:clientWidth={width} bind:clientHeight={height}>
  {#if TOKEN}
    <div class="map" bind:this={container} data-testid="map"></div>
    {#if !readOnly}
      <button class="recenter" type="button" onclick={recenter} aria-label="Recenter on home area">Recenter</button>
      {#if area}
        <!-- Remount on layout change so each layout keeps its own open/closed choice -->
        {#key compact}
          <Legend {visibleLabel} {compact} {outlineNames} />
        {/key}
      {/if}
    {/if}
    {#if !readOnly && tip && tipPoint}
      <div
        class="tooltip"
        class:left={tipLeft}
        class:heli={tipAircraft?.class === 'rotorcraft' || tipAircraft?.class === 'gyroplane'}
        style:left="{tipPoint.x}px"
        style:top="{tipY}px"
        role="tooltip"
        data-testid="map-tooltip"
      >
        <strong>{tip.title}{#if tip.tag}<span class="tag role-{tipAircraft?.role}">{tip.tag}</span>{/if}</strong>
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
  /* Zoom buttons in the dashboard's control style; Mapbox's icons are dark images, so redraw them as masks */
  .map :global(.mapboxgl-ctrl-group) {
    border: 1px solid var(--control-line);
    border-radius: 4px;
    background: rgba(5, 12, 8, 0.88);
    box-shadow: none;
    overflow: hidden;
  }
  .map :global(.mapboxgl-ctrl-group button) {
    width: 28px;
    height: 28px;
    background: transparent;
  }
  .map :global(.mapboxgl-ctrl-group button + button) {
    border-top: 1px solid var(--line);
  }
  .map :global(.mapboxgl-ctrl-group button:not(:disabled):hover) {
    background: var(--hover);
  }
  .map :global(.mapboxgl-ctrl-group button:focus-visible) {
    box-shadow: inset 0 0 0 1px var(--accent);
  }
  .map :global(.mapboxgl-ctrl-group .mapboxgl-ctrl-icon) {
    background: var(--muted-strong);
    mask: var(--icon) center / 14px 14px no-repeat;
  }
  .map :global(.mapboxgl-ctrl-group button:not(:disabled):hover .mapboxgl-ctrl-icon) {
    background: var(--text);
  }
  .map :global(.mapboxgl-ctrl-group button:disabled .mapboxgl-ctrl-icon) {
    background: var(--faint);
  }
  /* Attribution stays visible and legible (Mapbox requires it); only its colors change */
  .map :global(.mapboxgl-ctrl-attrib) {
    background: rgba(5, 12, 8, 0.75);
    color: var(--muted);
  }
  .map :global(.mapboxgl-ctrl-attrib a) {
    color: var(--muted-strong);
  }
  .map :global(.mapboxgl-ctrl-attrib a:hover) {
    color: var(--text);
  }
  .map :global(.mapboxgl-ctrl-zoom-in .mapboxgl-ctrl-icon) {
    --icon: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 14 14'%3E%3Cpath d='M6 1h2v5h5v2H8v5H6V8H1V6h5z'/%3E%3C/svg%3E");
  }
  .map :global(.mapboxgl-ctrl-zoom-out .mapboxgl-ctrl-icon) {
    --icon: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 14 14'%3E%3Cpath d='M1 6h12v2H1z'/%3E%3C/svg%3E");
  }
  .recenter {
    position: absolute;
    top: 10px;
    left: 10px;
    padding: 6px 10px;
    border: 1px solid var(--control-line);
    border-radius: 4px;
    background: rgba(5, 12, 8, 0.88);
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
    /* Without this, a card flipped left shrinks to the space between the aircraft and the edge */
    width: max-content;
    max-width: 230px;
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
  .tooltip .tag {
    margin-left: 6px;
    padding: 0 4px;
    border: 1px solid currentColor;
    border-radius: 3px;
    font-family: Inter, system-ui, sans-serif;
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.04em;
  }
  .tooltip .sub {
    color: var(--muted-strong);
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
