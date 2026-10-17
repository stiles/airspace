// Map symbols drawn on a canvas as signed-distance-field images, so the style
// can color them. All point north (up) at zero rotation.

import type { IconName } from './geo';

const SIZE = 64; // drawn at pixelRatio 2, displayed at 32px
export const ICON_PIXEL_RATIO = 2;

type Draw = (ctx: CanvasRenderingContext2D) => void;

const plane: Draw = (ctx) => {
  ctx.beginPath();
  // Fuselage, wings and tailplane
  ctx.moveTo(32, 6);
  ctx.lineTo(36, 14);
  ctx.lineTo(36, 26);
  ctx.lineTo(56, 38);
  ctx.lineTo(56, 43);
  ctx.lineTo(36, 37);
  ctx.lineTo(35, 50);
  ctx.lineTo(43, 56);
  ctx.lineTo(43, 59);
  ctx.lineTo(32, 56);
  ctx.lineTo(21, 59);
  ctx.lineTo(21, 56);
  ctx.lineTo(29, 50);
  ctx.lineTo(28, 37);
  ctx.lineTo(8, 43);
  ctx.lineTo(8, 38);
  ctx.lineTo(28, 26);
  ctx.lineTo(28, 14);
  ctx.closePath();
  ctx.fill();
};

const planeStill: Draw = (ctx) => {
  // No track: a direction-free marker
  ctx.beginPath();
  ctx.arc(32, 32, 9, 0, Math.PI * 2);
  ctx.fill();
  ctx.lineWidth = 4;
  ctx.beginPath();
  ctx.arc(32, 32, 17, 0, Math.PI * 2);
  ctx.stroke();
};

const rotor = (ctx: CanvasRenderingContext2D) => {
  ctx.lineWidth = 4;
  ctx.lineCap = 'round';
  ctx.beginPath();
  ctx.moveTo(12, 12);
  ctx.lineTo(52, 52);
  ctx.moveTo(52, 12);
  ctx.lineTo(12, 52);
  ctx.stroke();
  ctx.beginPath();
  ctx.ellipse(32, 30, 9, 12, 0, 0, Math.PI * 2);
  ctx.fill();
};

const heli: Draw = (ctx) => {
  rotor(ctx);
  // Tail boom behind the direction of travel
  ctx.fillRect(30, 40, 4, 18);
  ctx.fillRect(25, 55, 14, 4);
};

const heliStill: Draw = (ctx) => rotor(ctx);

const surface: Draw = (ctx) => {
  ctx.fillRect(22, 22, 20, 20);
};

const SHAPES: Record<IconName, Draw> = {
  plane,
  'plane-still': planeStill,
  heli,
  'heli-still': heliStill,
  surface,
};

export function drawIcon(name: IconName): ImageData | null {
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = SIZE;
  const ctx = canvas.getContext('2d');
  if (!ctx) return null;
  ctx.fillStyle = ctx.strokeStyle = '#fff';
  SHAPES[name](ctx);
  return ctx.getImageData(0, 0, SIZE, SIZE);
}

export const ICON_NAMES = Object.keys(SHAPES) as IconName[];
