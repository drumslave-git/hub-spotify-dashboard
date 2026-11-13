import type { Settings } from '../types';
import { at } from './environment';

function parts(d: Date, opts: Intl.DateTimeFormatOptions, tz?: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const p of new Intl.DateTimeFormat('en-GB', tz ? { ...opts, timeZone: tz } : opts).formatToParts(d)) {
    out[p.type] = p.value;
  }
  return out;
}

export interface ClockFace {
  hour: string; minute: string; ampm: string | null;
  weekday: string; day: string; month: string;
}

/** What the clock shows at `now` (ms, already corrected by the server's offset). */
export function clockFace(now: number, s: Settings): ClockFace {
  const d = new Date(now);
  const t = parts(d, s.hour12 ? { hour: '2-digit', minute: '2-digit', hour12: true }
                              : { hour: '2-digit', minute: '2-digit', hour12: false, hourCycle: 'h23' }, s.tz);
  if (at) {   // ?at=HH:MM shows that hour on the clock too (sky and clock agree)
    const h = at[0];
    t.hour = s.hour12 ? String((h + 11) % 12 + 1) : String(h).padStart(2, '0');
    t.minute = String(at[1]).padStart(2, '0');
    t.dayPeriod = h < 12 ? 'am' : 'pm';
  }
  const dp = parts(d, { weekday: 'long', day: 'numeric', month: 'long' }, s.tz);
  return {
    hour: t.hour, minute: t.minute, ampm: s.hour12 && t.dayPeriod ? t.dayPeriod : null,
    weekday: dp.weekday, day: dp.day, month: dp.month,
  };
}

/** The wall clock where the dashboard is, as "HH:MM" (24 h), ignoring ?at. */
export function wallTime(now: number, tz?: string): string {
  return new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit', hourCycle: 'h23', ...(tz ? { timeZone: tz } : {}) })
    .format(new Date(now));
}

/** Today's date and the minute of the day where the dashboard is (?at wins). */
export function localNow(now: number, tz?: string): { date: string; min: number } {
  const p = parts(new Date(now), { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }, tz);
  const min = at ? at[0] * 60 + at[1] : (+p.hour % 24) * 60 + +p.minute;
  return { date: `${p.year}-${p.month}-${p.day}`, min };
}

/** Minute of the day of an ISO local time ("2026-11-13T07:12"). */
export function minutes(iso: string): number {
  return +iso.slice(11, 13) * 60 + +iso.slice(14, 16);
}

/** Minutes from the start of `date` ("2026-11-13") to an ISO local time,
 *  so a time tomorrow is past 1440 and one yesterday is below 0. */
export function minutesFrom(date: string, iso: string): number {
  return (Date.parse(iso.slice(0, 10)) - Date.parse(date)) / 60000 + minutes(iso);
}

export function fmtMin(m: number, hour12: boolean): string {
  m = ((m % 1440) + 1440) % 1440;
  const h = Math.floor(m / 60) % 24, mm = String(m % 60).padStart(2, '0');
  return hour12 ? `${(h + 11) % 12 + 1}:${mm} ${h < 12 ? 'am' : 'pm'}` : `${String(h).padStart(2, '0')}:${mm}`;
}

/** A span of minutes: "1 h 20 min". */
export function dur(m: number): string {
  const h = Math.floor(m / 60);
  return (h ? `${h} h ` : '') + `${m % 60} min`;
}

/** A song position: "3:07". */
export function clock(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}
