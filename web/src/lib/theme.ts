// Radar-scope palette shared by the map. The interface uses the same values as CSS
// variables (App.svelte); keep the two in step.

export const SCOPE = {
  // Yellower lime than the basemap greens (hue ~150), so aircraft read against roads and labels
  phosphor: '#b8ff45', // aircraft, trails, visible boundary
  phosphorText: '#d4ffa0',
  ring: '#1f7a4a',
  ringLabel: '#3fbf7a',
  amber: '#ffb020', // helicopters
  unknown: '#5c8c72',
  surface: '#3c5a4a',
  accent: '#7fe7ff', // selection and home
  outline: '#4cc2d8', // boundaries such as city limits
  landmark: '#91b7aa',
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
  roadMinor: '#0b1d14',
  boundary: '#1a4d33',
  label: '#356b4f',
  labelPlace: '#4f9672',
  halo: '#030805',
};
