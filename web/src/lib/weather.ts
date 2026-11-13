import type { RainHour, Weather } from '../types';
import { params } from './environment';

export type Kind =
  | 'clearDay' | 'clearNight' | 'partlyDay' | 'partlyNight' | 'cloudy'
  | 'fog' | 'drizzle' | 'rain' | 'snow' | 'thunder';

export function kind(code: number, isDay: boolean | number): Kind {
  if (code === 0 || code === 1) return isDay ? 'clearDay' : 'clearNight';
  if (code === 2) return isDay ? 'partlyDay' : 'partlyNight';
  if (code === 3) return 'cloudy';
  if (code === 45 || code === 48) return 'fog';
  if (code >= 51 && code <= 57) return 'drizzle';
  if ((code >= 61 && code <= 67) || (code >= 80 && code <= 82)) return 'rain';
  if ((code >= 71 && code <= 77) || code === 85 || code === 86) return 'snow';
  if (code >= 95) return 'thunder';
  return 'cloudy';
}

const TEXT: Record<number, string> = {
  0: 'Clear', 1: 'Mostly clear', 2: 'Partly cloudy', 3: 'Overcast',
  45: 'Fog', 48: 'Freezing fog',
  51: 'Light drizzle', 53: 'Drizzle', 55: 'Heavy drizzle', 56: 'Freezing drizzle', 57: 'Freezing drizzle',
  61: 'Light rain', 63: 'Rain', 65: 'Heavy rain', 66: 'Freezing rain', 67: 'Freezing rain',
  71: 'Light snow', 73: 'Snow', 75: 'Heavy snow', 77: 'Snow grains',
  80: 'Light showers', 81: 'Showers', 82: 'Heavy showers', 85: 'Snow showers', 86: 'Heavy snow showers',
  95: 'Thunderstorm', 96: 'Thunderstorm, hail', 99: 'Thunderstorm, hail',
};
export const describe = (code: number) => TEXT[code] || '';

/** A temperature with a real minus sign: "−3°". */
export function deg(v: number): string {
  return Math.round(v).toString().replace('-', '−') + '°';
}

/** How much the clouds grey the sky (0..1). */
export function overcast(code: number | null | undefined): number {
  if (code == null || code <= 1) return 0;
  if (code === 2) return 0.25;
  if (code === 3) return 0.6;
  if (code === 45 || code === 48) return 0.7;
  if (code >= 95) return 0.75;
  return 0.62;
}

export type Falling = '' | 'rain' | 'rain light' | 'snow';
/** What falls from the sky. */
export function falling(code: number): Falling {
  if ((code >= 71 && code <= 77) || code === 85 || code === 86) return 'snow';
  if ((code >= 61 && code <= 67) || (code >= 80 && code <= 82) || code >= 95) return 'rain';
  if (code >= 51 && code <= 57) return 'rain light';
  return '';
}

const SNOW = [71, 73, 75, 77, 85, 86];

export interface RainOutlook {
  /** The headline: emphasised part and the rest. */
  strong: string; rest: string;
  /** A short line for the corner, or '' when dry. */
  short: string;
}

/** What the next 12 hours of rain chances add up to. */
export function rainOutlook(hours: RainHour[]): RainOutlook {
  let likely: RainHour | null = null, possible: RainHour | null = null;
  for (const h of hours) {
    if (!likely && h.chance >= 55) likely = h;
    if (!possible || h.chance > possible.chance) possible = h;
  }
  const what = (h: RainHour) => (SNOW.includes(h.code) ? 'Snow' : 'Rain');
  const hhmm = (h: RainHour) => h.time.slice(11, 16);
  const first = hours[0];
  if (likely && likely === first) return { strong: `${what(likely)} now`, rest: ` · ${likely.chance}%`, short: `${what(likely)} now` };
  if (likely) return { strong: `${what(likely)} likely around ${hhmm(likely)}`, rest: '', short: `${what(likely)} around ${hhmm(likely)}` };
  if (possible && possible.chance >= 25) {
    return { strong: '', rest: `${what(possible)} possible around ${hhmm(possible)} · ${possible.chance}%`,
             short: `${what(possible)} possible ${hhmm(possible)}` };
  }
  return { strong: '', rest: 'No rain in the next 12 hours', short: '' };
}

export const isWet = (h: RainHour) => h.chance >= 30 || h.mm >= 0.2;

/** ?weather=CODE and ?at=HH:MM: try other weather and hours on the real page. */
export function tryOut(w: Weather | null, sun: (w: Weather) => { rise: number; set: number } | null, min: number): Weather | null {
  if (!w) return w;
  const out = { ...w };
  if (params.get('weather')) out.code = +params.get('weather')!;
  if (params.get('at')) {
    const s = sun(out);
    if (s) out.is_day = min >= s.rise && min < s.set ? 1 : 0;
  }
  return out;
}
