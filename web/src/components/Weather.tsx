import type { RainHour, Weather } from '../types';
import { classNames } from '../lib/classNames';
import type { ReactNode } from 'react';
import { moonName, moonPath, type MoonPass, type SkyLook, type Sun } from '../lib/sky';
import { dur, fmtMin, minutes } from '../lib/time';
import { deg, describe, isWet, rainOutlook } from '../lib/weather';
import { WeatherIcon } from './icons';

/** The weather now: a moving icon, the temperature, what it is like. */
export function NowWeather({ w, compact = false }: { w: Weather | null; compact?: boolean }) {
  const today = w?.days?.[0];
  return (
    <div className="flex items-center gap-3.5">
      <div className={classNames('flex-none', compact ? 'size-[54px]' : 'size-14')}>
        {w && <WeatherIcon code={w.code} isDay={w.is_day} animated />}
      </div>
      <div className={classNames('leading-none font-light tracking-[-0.04em]', compact ? 'text-[64px]' : 'text-[68px]')}>
        {w ? deg(w.temp) : '--°'}
      </div>
      <div className="min-w-0 pl-0.5">
        <div className={classNames('whitespace-nowrap', compact ? 'text-[19px]' : 'text-[20px]')}>{w ? describe(w.code) : ' '}</div>
        <div className="mt-[3px] text-[15px] whitespace-nowrap text-muted">
          {!w ? ' ' : compact ? (
            `Feels ${deg(w.feels)}` + (today ? ` · ↑${deg(today.max)} ↓${deg(today.min)}` : '')
          ) : (
            <>
              Feels {deg(w.feels)} · {Math.round(w.wind)} m/s<br />
              {today ? `↑${deg(today.max)} ↓${deg(today.min)} · ` : ''}{w.place}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

/** The next two forecast hours, then the next two days, one row each. */
export function Forecast({ w, compact = false }: { w: Weather | null; compact?: boolean }) {
  if (!w) return <div className={compact ? '' : 'mt-3.5'} />;
  const rows = [
    ...(w.hours ?? []).slice(0, 2).map((h) => ({ when: h.time.slice(11, 16), code: h.code, isDay: h.is_day, hi: h.temp, lo: null as number | null })),
    ...(w.days ?? []).slice(1, 3).map((d) => ({
      when: new Intl.DateTimeFormat('en-GB', { weekday: 'short', timeZone: 'UTC' }).format(new Date(d.date + 'T12:00:00Z')),
      code: d.code, isDay: 1, hi: d.max, lo: d.min as number | null,
    })),
  ];
  return (
    <div className={compact ? '' : 'mt-3.5'}>
      {rows.map((r, i) => (
        <div key={i} className={classNames('flex items-center gap-3.5 border-t border-raised text-[18px] whitespace-nowrap', compact ? 'h-[38px]' : 'h-[42px]')}>
          <span className="w-14 text-[15px] text-muted">{r.when}</span>
          <span className="size-[22px] flex-none"><WeatherIcon code={r.code} isDay={r.isDay} /></span>
          <span className="min-w-0 flex-1 truncate">{describe(r.code)}</span>
          <span>{deg(r.hi)}{r.lo != null && <span className="ml-[0.3em] text-muted">{deg(r.lo)}</span>}</span>
        </div>
      ))}
    </div>
  );
}

/** Rain in the next 12 hours: one bar per hour, as tall as it is likely. */
export function RainChart({ hours }: { hours: RainHour[] }) {
  if (!hours.length) return null;
  const o = rainOutlook(hours);
  return (
    <div>
      <div className="text-[16px] whitespace-nowrap text-muted">
        {o.strong && <b className="font-normal text-cool">{o.strong}</b>}{o.rest}
      </div>
      <div className="mt-2 flex h-7 items-end gap-[5px]">
        {hours.map((h, i) => (
          <i key={i} className={classNames('min-h-[3px] flex-1 rounded-[3px]', isWet(h) ? 'bg-cool' : 'bg-line')}
             style={{ height: `${Math.max(8, h.chance)}%` }} />
        ))}
      </div>
      <div className="mt-1 flex text-[13px] text-muted">
        {hours.filter((_, i) => i % 3 === 0).map((h, i) => <span key={i} className="flex-1">{h.time.slice(11, 13)}</span>)}
      </div>
    </div>
  );
}

/** The rain line beside the player: '' when nothing is known. */
export function rainLine(w: Weather | null): { text: string; wet: boolean } {
  const hours = w?.rain ?? [];
  const short = hours.length ? rainOutlook(hours).short : '';
  if (short) return { text: short.includes(' now') ? short : short.replace(' around', ' likely around'), wet: true };
  return { text: hours.length ? 'No rain in the next 12 hours' : '', wet: false };
}

/** The minute of the next sunrise, if known. */
export function nextSunrise(sun: Sun | null, min: number): number | null {
  if (!sun) return null;
  return min < sun.rise ? sun.rise : sun.next?.sunrise ? minutes(sun.next.sunrise) : null;
}

/** Where a body is on the arc, 0 at the left end and 1 at the right. */
function arcPoint(f: number) {
  const a = Math.PI * (1 - Math.max(0, Math.min(1, f)));
  return { x: (170 + 150 * Math.cos(a)).toFixed(1), y: (88 - 76 * Math.sin(a)).toFixed(1) };
}

/** The path across the sky: the horizon, the whole way dotted, the part
 *  travelled drawn in, the body where it is now, and words under the ends. */
function Arc({ f, trail, body, left, middle, right }: {
  f: number; trail: string; body: ReactNode; left: string; middle: string; right: string;
}) {
  const up = f > 0 && f < 1, { x, y } = arcPoint(f);
  return (
    <div className="mb-auto text-[15px] text-muted">
      <svg viewBox="0 0 340 96" aria-hidden="true" className="block h-24 w-[340px] overflow-visible" fill="none" strokeLinecap="round">
        <line x1="0" y1="88" x2="340" y2="88" stroke="rgba(255,255,255,0.22)" strokeWidth={1} />
        <path d="M20 88 A150 76 0 0 1 320 88" stroke="rgba(255,255,255,0.3)" strokeWidth={1.5} strokeDasharray="2 5" />
        {up && <path d={`M20 88A150 76 0 0 1 ${x} ${y}`} className={trail} strokeWidth={2.5} />}
        {up && <g transform={`translate(${x} ${y})`}>{body}</g>}
      </svg>
      <div className="mt-1.5 flex justify-between whitespace-nowrap">
        <span><b className="font-normal text-fg">{left}</b></span>
        <span>{middle}</span>
        <span><b className="font-normal text-fg">{right}</b></span>
      </div>
    </div>
  );
}

/** The path across the sky now: the sun's while it is up; after dark the
 *  moon's, if it is up or rises before the sun does. */
export function SkyArc({ sun, sky, moon, phase, min, hour12 }: {
  sun: Sun | null; sky: SkyLook; moon: MoonPass | null; phase: number; min: number; hour12: boolean;
}) {
  if (!sun) return null;
  const { rise, set, f } = sky;
  const sunUp = f > 0 && f < 1;
  const next = nextSunrise(sun, min);
  const sunriseIn = next == null ? null : min < rise ? rise - min : 1440 - min + next;

  if (!sunUp && moon && moon.rise < min + (sunriseIn ?? 720)) {
    const moonUp = moon.rise <= min;
    return (
      <Arc f={(min - moon.rise) / (moon.set - moon.rise)} trail="stroke-white/70"
           left={fmtMin(moon.rise, hour12)} right={fmtMin(moon.set, hour12)}
           middle={!moonUp ? `Moonrise in ${dur(moon.rise - min)}`
                 : moon.set - min <= 150 ? `Moonset in ${dur(moon.set - min)}` : moonName(phase)}
           body={<>
             <circle r="13" fill="white" opacity={0.14} />
             <g transform="scale(0.8) translate(-12 -12)">
               <circle cx="12" cy="12" r="10" fill="rgba(255,255,255,0.16)" />
               <path d={moonPath(phase)} fill="#f3efe4" />
             </g>
           </>} />
    );
  }
  return (
    <Arc f={f} trail="stroke-warm" left={fmtMin(rise, hour12)} right={fmtMin(set, hour12)}
         middle={sunUp ? (set - min <= 150 ? `Sunset in ${dur(set - min)}` : `${dur(set - rise)} of daylight`)
               : sunriseIn == null ? 'After sunset' : `Sunrise in ${dur(sunriseIn)}`}
         body={<>
           <circle r="13" className="fill-warm" opacity={0.25} />
           <circle r="6.5" className="fill-warm" />
         </>} />
  );
}
