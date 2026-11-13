import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { flushSync } from 'react-dom';
import type { Devices, Library, NightHours, ServerState, Settings, Weather } from './types';
import { Time, DateLine } from './components/Clock';
import { Bar, PlayerPanel } from './components/Player';
import { Sheet, type SheetRow, type SheetState } from './components/Sheet';
import { Sky } from './components/Sky';
import { Forecast, nextSunrise, NowWeather, RainChart, rainLine, SunArc } from './components/Weather';
import { soundRunning, analyser, unlockSound } from './lib/audio';
import { cn } from './lib/cn';
import { DEMO_DEVICES, DEMO_LIBRARY } from './lib/demo';
import { beat, diag, startDiagnostics } from './lib/diag';
import { demo } from './lib/env';
import { useMusic } from './lib/music';
import { usePlayer, type Action } from './lib/player';
import { moonName, moonPath, moonPhase, skyLook, sunToday } from './lib/sky';
import { clockFace, fmtMin, localNow, wallTime } from './lib/time';
import { useTint } from './lib/tint';
import { tryOut } from './lib/weather';

/** The clock: ticks every second, and notices when the page was frozen. */
function useNow(): number {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    let lastTickAt = performance.now();
    const timer = setInterval(() => {
      const at = performance.now(), gap = (at - lastTickAt) / 1000;
      lastTickAt = at;
      if (gap > 5) {
        diag.timers.gaps++;
        diag.timers.maxGap = Math.max(diag.timers.maxGap, Math.round(gap));
        if (gap > 20) beat(`timers-paused: ${Math.round(gap)} s`);
      }
      setNow(Date.now());
    }, 1000);
    return () => clearInterval(timer);
  }, []);
  return now;
}

interface Server { settings: Settings; night: NightHours | null; weather: Weather | null; lastOk: number }

/** Settings, weather and the server's clock, refreshed every minute. When the
 *  page itself has changed on the server, it reloads. */
function useServer(): Server {
  const [server, setServer] = useState<Server>({ settings: { hour12: false, offset: 0 }, night: null, weather: null, lastOk: 0 });
  useEffect(() => {
    let version: string | null = null;
    const refresh = () => {
      const sent = Date.now();
      fetch('/api/state', { cache: 'no-store' })
        .then((r) => r.json())
        .then((s: ServerState) => {
          const rtt = Date.now() - sent;
          let offset = s.now + rtt / 2 - Date.now();
          if (Math.abs(offset) < 2000) offset = 0;   // device clock is fine, don't jitter
          if (version && s.version !== version) { location.reload(); return; }
          version = s.version;
          setServer({ settings: { tz: s.timezone, hour12: !!s.hour12, offset }, night: s.night ?? null, weather: s.weather, lastOk: Date.now() });
        })
        .catch(() => { /* keep showing the last data */ });
    };
    refresh();
    const timer = setInterval(refresh, 60 * 1000);
    return () => clearInterval(timer);
  }, []);
  return server;
}

/** The layout is drawn for 1024 x 600 (the Hub); scale it to fit any window. */
function useStageScale(): number {
  const fit = () => Math.min(innerWidth / 1024, innerHeight / 600);
  const [scale, setScale] = useState(fit);
  useEffect(() => {
    // At once, not on React's next turn: a screenshot may follow straight away.
    const onResize = () => flushSync(() => setScale(fit()));
    setScale(fit());   // the window may have changed before this ran
    addEventListener('resize', onResize);
    return () => removeEventListener('resize', onResize);
  }, []);
  diag.stageScale = scale;
  return scale;
}

// Greys become see-through white on the sky, so they read on any colour.
const onSky = '[--color-muted:rgba(255,255,255,0.66)] [--color-line:rgba(255,255,255,0.2)] [--color-raised:rgba(255,255,255,0.16)] text-shadow-[0_1px_14px_rgba(0,0,0,0.22)]';
const skyColon = 'text-white/40';

export default function App() {
  const nowDevice = useNow();
  const { settings, night, weather: rawWeather, lastOk } = useServer();
  const scale = useStageScale();
  const offsetRef = useRef(0);
  offsetRef.current = settings.offset;
  const { player, note, barArtist, npOn, busy, command } = usePlayer(offsetRef);
  const [wakeUntil, setWakeUntil] = useState(0);
  const [sheet, setSheet] = useState<SheetState | null>(null);

  const now = nowDevice + settings.offset;
  const face = clockFace(now, settings);
  const local = localNow(now, settings.tz);
  const minuteKey = `${local.date} ${local.min}`;

  // The sky and everything timed by the sun change once a minute.
  const { weather, sun, sky } = useMemo(() => {
    const sunOf = (w: Weather) => sunToday(w, local.date);
    const weather = tryOut(rawWeather, sunOf, local.min);
    const sun = sunToday(weather, local.date);
    return { weather, sun, sky: skyLook(weather, local.min, sun) };
  }, [rawWeather, minuteKey]);

  // The night clock: the moon tonight and when it gets light.
  const phase = moonPhase(now);
  const sunrise = nextSunrise(sun, local.min);
  diag.sky = { sky: sky.name, cloud: sky.cloud, falling: sky.falling, moon: moonName(phase),
               sun: sun ? `${fmtMin(sun.rise, settings.hour12)}-${fmtMin(sun.set, settings.hour12)}` : null };

  // Between the set hours the page shows only a dim clock, unless music is
  // playing. A touch brings the full dashboard back for a minute.
  let nightOn = demo === 'night';
  if (!demo && night?.from && night.to && night.from !== night.to) {
    const t = wallTime(now, settings.tz);
    const inside = night.from < night.to ? t >= night.from && t < night.to : t >= night.from || t < night.to;
    nightOn = inside && Date.now() > wakeUntil && !(player?.active && player.playing);
  }

  // The receiver and the heartbeat start once the page is on screen.
  useEffect(startDiagnostics, []);

  useEffect(() => {
    const onTouch = () => {
      diag.touches++;
      setWakeUntil(Date.now() + 60 * 1000);
      unlockSound();   // any touch unlocks sound for the in-page player
    };
    document.addEventListener('pointerdown', onTouch);
    return () => document.removeEventListener('pointerdown', onTouch);
  }, []);

  // Colour of the cover, and the music made visible.
  const ready = !!player?.connected, active = ready && !!player?.active;
  const shown = active ? player : ready ? player?.last ?? null : null;
  const art = ready ? shown?.art ?? null : null;
  const tint = useTint(art);
  const canvas = useRef<HTMLCanvasElement>(null), glow = useRef<HTMLDivElement>(null), artRef = useRef<HTMLDivElement>(null);
  const music = !!(player?.active && player.playing && npOn && !nightOn &&
                   (demo || (analyser && player.on_dashboard && soundRunning())));
  useMusic(music, tint.hi, canvas, glow, artRef);

  // ----------------------------------------------- what to play, and where
  const playerRef = useRef(player);
  playerRef.current = player;
  const closeSheet = useCallback(() => setSheet(null), []);
  const openSheet = useCallback((kind: 'library' | 'devices') => {
    setSheet({ kind, rows: [], page: 0, empty: 'Loading…' });
    const load: Promise<Library | Devices> = demo
      ? Promise.resolve(kind === 'devices' ? DEMO_DEVICES : DEMO_LIBRARY)
      : fetch(kind === 'devices' ? '/api/devices' : '/api/library', { cache: 'no-store' }).then((r) => r.json());
    load.then((data) => {
      let rows: SheetRow[], empty: string;
      if (kind === 'devices') {
        rows = ((data as Devices).devices ?? []).map((d) => ({
          value: d.id, name: d.name, icon: 'device', now: d.active,
          sub: d.active ? 'Playing here now' : d.here ? 'This display' : d.type || 'Device',
        }));
        empty = 'Spotify sees no devices right now.';
      } else {
        const lib = data as Library, seen = new Set<string>();
        rows = [
          ...(lib.recent ?? []).map((e): SheetRow => {
            seen.add(e.uri);
            return { value: e.uri, name: e.name, art: e.art, icon: 'note', sub: `${e.kind} · played recently` };
          }),
          ...(lib.playlists ?? []).filter((e) => !seen.has(e.uri))
            .map((e): SheetRow => ({ value: e.uri, name: e.name, art: e.art, icon: 'note', sub: e.kind })),
        ];
        if (lib.liked) rows.unshift({ value: lib.liked.uri, name: lib.liked.name, art: lib.liked.art, icon: 'note', sub: lib.liked.kind });
        empty = 'No playlists found.';
        // Signed in before the dashboard asked for playlists and likes.
        if (playerRef.current?.needs_login) {
          rows.unshift({ value: null, name: 'Sign in again to see your playlists', icon: 'note',
                         sub: `Open ${location.host}/api/spotify/login` });
        }
      }
      setSheet((s) => (s?.kind === kind ? { ...s, rows, empty } : s));
    }).catch(() => setSheet((s) => (s?.kind === kind ? { ...s, empty: 'The dashboard server is not responding.' } : s)));
  }, []);
  const pick = (row: SheetRow) => {
    if (!sheet || row.value == null) return;   // a hint, not a choice
    const action: Action = sheet.kind === 'devices' ? 'device' : 'play';
    setSheet(null);
    command(action, row.value);
  };

  const stale = !!lastOk && Date.now() - lastOk >= 5 * 60 * 1000;
  const barTitle = !ready ? "Spotify isn't connected" : shown ? shown.title ?? '' : 'Nothing played yet';
  const npRain = rainLine(weather);

  return (
    <>
      <div id="stage"
           className={cn('absolute top-1/2 left-1/2 -mt-[300px] -ml-[512px] h-[600px] w-[1024px] overflow-hidden tabular-nums', nightOn && 'invisible')}
           style={{ transform: `scale(${scale})`, '--cover': sky.cover } as CSSProperties}>
        <Sky look={sky} />

        {/* The bar and the player's panel are one tinted surface: it grows
            from the bar into the panel while the home view fades out, then
            the player's words fade in on it. Stopping runs the same way back. */}
        <div className={cn('absolute inset-0 bg-tint',
          npOn ? '[clip-path:inset(0px_0px_0px_400px_round_0px)] [transition:clip-path_0.6s_cubic-bezier(0.65,0,0.35,1),background-color_0.8s]'
               : '[clip-path:inset(468px_32px_28px_32px_round_26px)] [transition:clip-path_0.6s_cubic-bezier(0.65,0,0.35,1)_0.15s,background-color_0.8s]',
          'motion-reduce:[transition:background-color_0.8s]')} />

        {/* Home: time, weather, and the player waiting as one bar. */}
        <section id="home" className={cn('absolute inset-0 flex flex-col gap-[22px] px-8 pt-[30px] pb-7', onSky,
                                          npOn && 'invisible [transition:visibility_0s_0.25s]')}>
          <div className="flex min-h-0 flex-1 justify-between gap-10 px-1">
            <div className={cn('flex min-w-0 flex-col justify-between transition-opacity',
                               npOn ? 'opacity-0 duration-250' : 'duration-400 delay-150')}>
              <div>
                <Time id="time" face={face} colon={skyColon} className="text-[196px] leading-[0.84] tracking-[-0.05em]" />
                <DateLine face={face} className="mt-[22px] text-[24px]" />
              </div>
              <NowWeather w={weather} />
            </div>
            <div className={cn('flex w-[340px] flex-none flex-col justify-end transition-opacity',
                               npOn ? 'opacity-0 duration-250' : 'duration-400 delay-450')}>
              <SunArc sun={sun} sky={sky} min={local.min} hour12={settings.hour12} />
              <RainChart hours={weather?.rain ?? []} />
              <Forecast w={weather} />
            </div>
          </div>
          <Bar player={player} title={barTitle} artist={barArtist} note={note} busy={busy} art={art}
               command={command} open={openSheet}
               className={cn('transition-opacity', npOn ? 'opacity-0 duration-250' : 'duration-300 delay-600')} />
        </section>

        {/* Music playing: time and weather left, a cover-tinted panel right. */}
        <section id="player" className={cn('absolute inset-0 grid grid-cols-[400px_624px]',
                                            npOn ? 'visible' : 'invisible [transition:visibility_0s_0.25s]')}>
          <div className={cn('flex min-w-0 flex-col justify-between pt-[30px] pr-7 pb-7 pl-8', onSky,
                             npOn ? 'opacity-100 [transition:opacity_0.45s_0.25s]' : 'opacity-0 [transition:opacity_0.25s]')}>
            <div>
              <Time face={face} colon={skyColon} className="text-[128px] leading-[0.88] tracking-[-0.045em]" />
              <DateLine face={face} className="mt-4 text-[21px]" />
            </div>
            <div>
              <NowWeather w={weather} compact />
              <div className={cn('mt-3.5 text-[15px] whitespace-nowrap', npRain.wet ? 'text-cool' : 'text-muted')}>{npRain.text}</div>
            </div>
            <Forecast w={weather} compact />
          </div>
          <PlayerPanel player={player} note={note} busy={busy} offset={settings.offset} command={command} open={openSheet}
                       shown={npOn} canvas={canvas} glow={glow} art={artRef} />
        </section>

        {sheet && <Sheet sheet={sheet} setPage={(page) => setSheet({ ...sheet, page })} pick={pick} close={closeSheet} />}
      </div>

      {/* Night: a dim clock only. */}
      {nightOn && (
        <div id="night" className="fixed inset-0 flex flex-col items-center justify-center bg-black text-[#4a4d52]">
          <Time face={face} colon="text-[#2e3034]" className="text-[40vh] leading-[0.84] tracking-[-0.05em]" />
          <DateLine face={face} className="mt-[22px] text-[4.6vh]" tone="text-[#33363a]" strong="text-[#4a4d52]" />
          <div className="mt-[4.5vh] flex items-center gap-[1.6vh] text-[3.6vh] text-[#33363a]">
            <svg viewBox="0 0 24 24" aria-hidden="true" className="size-[5vh]">
              <circle cx="12" cy="12" r="10" fill="#121314" stroke="#222427" strokeWidth={0.6} />
              <path d={moonPath(phase)} fill="#4a4d52" />
            </svg>
            <span>
              {moonName(phase)}
              {sunrise != null && <> · sunrise <b className="font-normal text-[#4a4d52]">{fmtMin(sunrise, settings.hour12)}</b></>}
            </span>
          </div>
        </div>
      )}

      {stale && <div className="fixed bottom-1 left-3 text-[14px] text-muted">offline</div>}
    </>
  );
}
