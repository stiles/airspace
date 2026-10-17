// Map configuration. The token stays in web/.env.local (gitignored).
export const MAPBOX_TOKEN = import.meta.env.VITE_MAPBOX_TOKEN as string | undefined;

// dark-v11 is recolored into a radar scope at load (lib/basemap.ts). Standard styles also work
// (e.g. mapbox://styles/stiles/cmv5o51lp007201rogm24804g) but keep their own colors.
export const MAPBOX_STYLE = (import.meta.env.VITE_MAPBOX_STYLE as string | undefined) || 'mapbox://styles/mapbox/dark-v11';

// Used only if the style has no symbol layer to borrow a font stack from
export const FALLBACK_FONT = ['Open Sans Regular', 'Arial Unicode MS Regular'];
