// The background follows the real sky: night, blue hour, dawn, day, golden
// hour, sunset and dusk, timed from today's sunrise and sunset and greyed by
// clouds, with stars at night and falling rain or snow when it does.
import type { DayForecast, Weather } from '../types';
import { minutes } from './time';
import { falling, overcast, type Falling } from './weather';

type RGB = [number, number, number];

const SKY = {   // top, middle, bottom of the screen
  night:   ['#04060c', '#0a1122', '#151d35'],
  blue:    ['#0a1230', '#1d2756', '#3b3d72'],
  dawn:    ['#1a2858', '#634878', '#c47862'],
  morning: ['#163e6c', '#2f6195', '#6b8fb4'],
  day:     ['#10335d', '#22548a', '#3b6fa0'],
  golden:  ['#18365f', '#5f5a73', '#b0764a'],
  sunset:  ['#181d44', '#5f3560', '#ad5049'],
  dusk:    ['#0c1230', '#252352', '#463359'],
} as const;
type Phase = keyof typeof SKY;

export const rgb = (c: string): RGB => [parseInt(c.slice(1, 3), 16), parseInt(c.slice(3, 5), 16), parseInt(c.slice(5, 7), 16)];
export const mix = (a: RGB, b: RGB, t: number): RGB => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
export const css = (c: RGB) => `rgb(${c.map(Math.round).join(',')})`;

function grey(c: RGB, amount: number): RGB {
  const l = (0.3 * c[0] + 0.59 * c[1] + 0.11 * c[2]) * (1 - amount * 0.25);
  return mix(c, [l * 0.94, l * 0.98, l * 1.08], amount);
}

export interface Sun { rise: number; set: number; next?: DayForecast }

/** Today's sunrise and sunset, in minutes of the day, if the forecast has them. */
export function sunToday(w: Weather | null, date: string): Sun | null {
  const days = w?.days ?? [];
  for (let i = 0; i < days.length; i++) {
    const d = days[i];
    if (d.date === date && d.sunrise && d.sunset) return { rise: minutes(d.sunrise), set: minutes(d.sunset), next: days[i + 1] };
  }
  return null;
}

function skyAt(m: number, rise: number, set: number): { name: Phase; cols: RGB[] } {
  const noon = (rise + set) / 2;
  const keys: [number, Phase][] = [
    [rise - 90, 'night'], [rise - 40, 'blue'], [rise + 5, 'dawn'],
    [Math.min(rise + 60, noon), 'morning'], [Math.min(rise + 150, noon), 'day'],
    [Math.max(set - 150, noon), 'day'], [Math.max(set - 60, noon), 'golden'],
    [set, 'sunset'], [set + 35, 'dusk'], [set + 90, 'night'],
  ];
  for (let i = 1; i < keys.length; i++) {
    const [a0, a] = keys[i - 1], [b0, b] = keys[i];
    if (m >= a0 && m < b0) {
      let t = b0 > a0 ? (m - a0) / (b0 - a0) : 1;
      t = t * t * (3 - 2 * t);   // ease, so a phase lingers before the next
      return { name: t < 0.5 ? a : b, cols: [0, 1, 2].map((k) => mix(rgb(SKY[a][k]), rgb(SKY[b][k]), t)) };
    }
  }
  return { name: 'night', cols: SKY.night.map(rgb) };
}

export interface SkyLook {
  name: Phase;
  gradient: string;
  /** A cloud in a weather icon is filled with the sky around it, a touch lighter. */
  cover: string;
  /** The sun's glow: where, what colour, how strong (0 hides it). */
  glow: { x: number; y: number; color: string; opacity: number };
  stars: number;
  cloud: number;
  falling: Falling;
  /** How far through the day the sun is: 0 at sunrise, 1 at sunset. */
  f: number;
  rise: number;
  set: number;
}

export function skyLook(w: Weather | null, min: number, sun: Sun | null): SkyLook {
  const rise = sun ? sun.rise : 7 * 60, set = sun ? sun.set : 17 * 60;
  const sky = skyAt(min, rise, set), cloud = overcast(w?.code);
  const cols = sky.cols.map((c) => grey(c, cloud));

  // The sun's glow, low and warm at the ends of the day, pale at noon.
  const f = (min - rise) / (set - rise);
  let glow = { x: 0, y: 0, color: '#ffb070', opacity: 0 };
  if (f > -0.06 && f < 1.06) {
    const h = Math.sin(Math.PI * Math.max(0, Math.min(1, f)));
    glow = {
      x: Math.round(1024 * (0.04 + 0.92 * f)), y: Math.round(640 - 520 * h),
      color: css(mix([255, 168, 100], [255, 240, 214], h)),
      opacity: (0.4 - 0.22 * h) * (1 - cloud * 0.8),
    };
  }
  const dark = Math.max(0, Math.min(1, Math.max((rise - 20 - min) / 40, (min - set - 20) / 40)));
  return {
    name: sky.name,
    gradient: `linear-gradient(180deg,${css(cols[0])} 0%,${css(cols[1])} 55%,${css(cols[2])} 100%)`,
    cover: css(mix(mix(cols[1], cols[2], 0.4), [255, 255, 255], 0.08)),
    glow,
    stars: dark * Math.max(0, 1 - cloud * 1.4),
    cloud,
    falling: w ? falling(w.code) : '',
    f, rise, set,
  };
}

// ------------------------------------------------------------------ moon
/** Days since the last new moon, as a part of the moon's cycle (0 = new, 0.5 = full). */
export function moonPhase(now: number): number {
  const p = ((now - Date.UTC(2000, 0, 6, 18, 14)) / 86400000 / 29.530588853) % 1;
  return p < 0 ? p + 1 : p;
}

export function moonName(p: number): string {
  return p < 0.03 || p > 0.97 ? 'New moon' : p < 0.22 ? 'Waxing crescent' : p < 0.28 ? 'First quarter'
    : p < 0.47 ? 'Waxing gibbous' : p < 0.53 ? 'Full moon' : p < 0.72 ? 'Waning gibbous'
    : p < 0.78 ? 'Last quarter' : 'Waning crescent';
}

/** The lit part of the moon as seen from the northern hemisphere: the lit
 *  edge, then the shadow line back, an ellipse as wide as the phase makes it. */
export function moonPath(p: number): string {
  const k = (10 * Math.abs(Math.cos(2 * Math.PI * p))).toFixed(2);
  const waxing = p < 0.5, crescent = p < 0.25 || p > 0.75;
  return `M12 2A10 10 0 0 ${waxing ? 1 : 0} 12 22A${k} 10 0 0 ${waxing === crescent ? 0 : 1} 12 2z`;
}

// ------------------------------------------------- rain, snow and stars
// Patterns for rain and snow, each layer sliding down by one tile, and the
// stars: always the same, from a fixed seed.
let seed = 11;
function rnd() { seed = (seed * 16807) % 2147483647; return seed / 2147483647; }

function tile(size: number, body: string) {
  return `url("data:image/svg+xml,${encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}">${body}</svg>`)}")`;
}
function streaks(size: number, n: number, len: [number, number], alpha: number, width: number) {
  let out = '';
  for (let i = 0; i < n; i++) {
    const l = len[0] + rnd() * (len[1] - len[0]), x = (4 + rnd() * (size - 8)).toFixed(1), y = (rnd() * (size - l)).toFixed(1);
    out += `<line x1="${x}" y1="${y}" x2="${x}" y2="${(+y + l).toFixed(1)}" stroke="rgba(214,228,255,${alpha})" stroke-width="${width}" stroke-linecap="round"/>`;
  }
  return tile(size, out);
}
function flakes(size: number, n: number, r: [number, number], alpha: number) {
  let out = '';
  for (let i = 0; i < n; i++) {
    const rr = r[0] + rnd() * (r[1] - r[0]);
    out += `<circle cx="${(rr + rnd() * (size - 2 * rr)).toFixed(1)}" cy="${(rr + rnd() * (size - 2 * rr)).toFixed(1)}" r="${rr.toFixed(1)}" fill="rgba(255,255,255,${alpha})"/>`;
  }
  return tile(size, out);
}

export const TILES = {
  rain1: streaks(200, 9, [16, 26], 0.42, 1.3),
  rain2: streaks(300, 8, [28, 42], 0.28, 1),
  snow1: flakes(200, 9, [1.8, 2.8], 0.85),
  snow2: flakes(300, 16, [1, 1.7], 0.6),
};

// Stars, more of them high up: three sizes, each twinkling at its own pace.
export const STARS = [0, 1, 2].map(() => {
  const list: string[] = [];
  for (let i = 0; i < 26; i++) {
    list.push(`${Math.round(rnd() * 1024)}px ${Math.round(rnd() * rnd() * 430)}px 0 rgba(255,255,255,${(0.4 + rnd() * 0.55).toFixed(2)})`);
  }
  return list.join(',');
});
