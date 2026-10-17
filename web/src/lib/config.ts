// Map configuration. The token stays in web/.env.local (gitignored).
export const MAPBOX_TOKEN = import.meta.env.VITE_MAPBOX_TOKEN as string | undefined;

// The default style is private to the stiles Mapbox account; others can set VITE_MAPBOX_STYLE,
// e.g. mapbox://styles/mapbox/dark-v11
export const MAPBOX_STYLE =
  (import.meta.env.VITE_MAPBOX_STYLE as string | undefined) || 'mapbox://styles/stiles/cmv5o51lp007201rogm24804g';

// Used only if the style has no symbol layer to borrow a font stack from
export const FALLBACK_FONT = ['Open Sans Regular', 'Arial Unicode MS Regular'];
