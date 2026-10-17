// Radar-scope palette shared by the map. The interface uses the same values as CSS
// variables (App.svelte); keep the two in step.

export const SCOPE = {
  phosphor: '#3dff8f', // aircraft, visible boundary
  phosphorText: '#a6f7c6',
  ring: '#1f7a4a',
  ringLabel: '#3fbf7a',
  amber: '#ffb020', // helicopters
  unknown: '#5c8c72',
  surface: '#3c5a4a',
  accent: '#7fe7ff', // selection and home
  halo: '#030805',
};

/** Colors for recoloring a classic Mapbox style (e.g. dark-v11) into a quiet scope. */
export const BASEMAP = {
  land: '#040a07',
  water: '#010503',
  landuse: '#06100b',
  building: '#0a1811',
  road: '#0d2419',
  roadMajor: '#14402a',
  boundary: '#1a4d33',
  label: '#356b4f',
  labelPlace: '#4f9672',
  halo: '#030805',
};
