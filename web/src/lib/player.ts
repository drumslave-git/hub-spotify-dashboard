// The Spotify player: polled from the server every two seconds, with each
// press shown straight away and confirmed by the next poll.
import { useEffect, useReducer, useRef, type RefObject } from 'react';
import type { Player } from '../types';
import { cutSound, onSoundChange, reloadForSound, soundBlocked, startSound, unlockSound } from './audio';
import { DEMO } from './demo';
import { diag } from './diag';
import { demo } from './env';

/** Paused for longer than this, the screen goes back to the clock. */
const PAUSE_KEEP = 3 * 60 * 1000;
/** Settle at once, no need to wait in line. */
const QUICK = new Set(['like', 'shuffle', 'repeat', 'seek']);

export type Action = 'toggle' | 'prev' | 'next' | 'like' | 'shuffle' | 'repeat' | 'seek' | 'play' | 'device';
export interface Note { text: string; alert: boolean }

/** Where the song is now, in ms. */
export function position(p: Player, offset: number): number {
  const pos = (p.progress_ms ?? 0) + (p.playing ? Date.now() + offset - (p.at ?? 0) : 0);
  return Math.max(0, Math.min(p.duration_ms ?? 0, pos));
}

export function usePlayer(offset: RefObject<number>) {
  const [, redraw] = useReducer((x: number) => x + 1, 0);
  const s = useRef({
    player: null as Player | null,
    note: { text: '', alert: false } as Note,
    barArtist: '',
    npOn: false,
    busy: false,
    holdUntil: 0, alertUntil: 0, pausedAt: 0,
    busyTimer: 0,
  }).current;

  // One status line, shown under the title while playing and in the bar
  // otherwise (after the artist). An error stays visible for a while.
  function note(text: string | undefined, alert = false) {
    if (!alert && Date.now() < s.alertUntil) return;
    s.note = { text: text || '', alert };
    if (alert) s.alertUntil = Date.now() + 6000;
  }

  function show(p: Player | null, draw = true) {
    s.player = p;
    const ready = !!p?.connected;
    if (p?.local && p.local.state !== 'off') startSound();
    const active = ready && !!p!.active;

    // Playing, or only just paused: the music takes the screen.
    if (active && !p!.playing) { if (!s.pausedAt) s.pausedAt = Date.now(); }
    else s.pausedAt = 0;
    s.npOn = active && (!!p!.playing || Date.now() - s.pausedAt < PAUSE_KEEP);

    if (!p || !ready) {
      s.barArtist = p?.configured ? 'Sign in once to turn the player on' : 'Add the Spotify app ID to finish setup';
      note(p?.error ?? '', !!p?.error);
    } else {
      const last = active ? p : p.last;
      s.barArtist = last?.artist ?? '';
      const local = p.local ?? { state: 'off' };
      note(p.error ? p.error
           : active ? (p.device ? 'Playing on ' + p.device : '')
           : p.local_ready ? 'tap play to pick up where you left off'
           : local.state === 'error' ? (local.message || 'The player could not start')
           : local.state === 'off' ? 'This server has no player of its own'
           : local.state === 'ready' ? 'Connecting the sound…'
           : 'The player is starting…',
           !!p.error);
      if (active && !p.playing) note(p.device ? 'Paused on ' + p.device : 'Paused');
      // Music is meant to come out of this device, but the browser holds the
      // sound back until the screen is touched. Ask anyway first: some Hubs
      // let sound start without a touch.
      if (active && p.playing && p.on_dashboard && soundBlocked()) {
        unlockSound();
        reloadForSound();
        note('Tap the screen to hear it here', true);
      }
    }
    diag.view = s.npOn ? 'playing' : 'home';
    diag.player = !p ? 'none' : !p.connected ? 'not-connected'
      : p.active ? `${p.playing ? 'playing' : 'paused'}:${p.device}` : 'idle';
    if (draw) redraw();
  }

  function poll() {
    if (demo) { if (!s.player) show(DEMO[demo] ?? DEMO.setup); return; }
    if (Date.now() < s.holdUntil) return;   // let a just-sent command take effect first
    fetch('/api/player', { cache: 'no-store' })
      .then((r) => r.json())
      .then((p: Player) => { if (Date.now() >= s.holdUntil) show(p); })
      .catch(() => {});
  }

  // One press at a time: starting music takes a second or two, and extra
  // impatient taps would otherwise each flip play and pause again.
  function setBusy(on: boolean) {
    s.busy = on;
    clearTimeout(s.busyTimer);
    if (on) s.busyTimer = window.setTimeout(() => setBusy(false), 6000);
    redraw();
  }

  function command(action: Action, value?: string | number | null) {
    const old = s.player;
    if (!old || !old.connected) return;
    if (s.busy && !QUICK.has(action)) return;
    const wasPlaying = !!(old.active && old.playing);
    const now = Date.now() + (offset.current ?? 0);
    // Show the result straight away; the next poll confirms the real state.
    const p: Player = { ...old };
    if (action === 'toggle' && p.active) {
      if (p.playing) p.progress_ms = position(p, offset.current ?? 0);
      p.at = now;
      p.playing = !p.playing;
    }
    if (action === 'like') p.liked = !p.liked;
    if (action === 'shuffle') p.shuffle = !p.shuffle;
    if (action === 'repeat') p.repeat = ({ off: 'context', context: 'track', track: 'off' } as const)[p.repeat ?? 'off'] ?? 'context';
    if (action === 'seek') { p.progress_ms = value as number; p.at = now; }
    show(p);
    if ((action === 'toggle' && !wasPlaying) || action === 'play') note('Starting…');
    if (action === 'device') note('Moving the music…');
    s.holdUntil = Date.now() + 2500;
    redraw();
    if (demo) return;
    if (!QUICK.has(action)) setBusy(true);

    // Being inside a tap is what lets the browser start sound.
    unlockSound();
    // Pausing, skipping or jumping in music that plays here: cut the queued
    // sound at once instead of letting the last half second trail on.
    const cuts = action === 'next' || action === 'prev' || action === 'seek' || action === 'play' ||
                 action === 'device' || (action === 'toggle' && wasPlaying);
    if (p.on_dashboard && cuts) cutSound(600);
    fetch('/api/player', { method: 'POST', body: JSON.stringify({ action, value }) })
      .then((r) => r.json())
      .then((res: { ok: boolean; message?: string }) => {
        if (!res.ok) { s.holdUntil = 0; poll(); note(res.message || 'That did not work.', true); redraw(); }
      })
      .catch(() => { s.holdUntil = 0; note('The dashboard server is not responding.', true); redraw(); })
      .then(() => { if (!QUICK.has(action)) setBusy(false); });
    setTimeout(poll, 2600);
  }

  // `show` and `poll` only touch `s` and `redraw`, both stable: the first
  // render's copies are as good as any.
  const live = useRef({ show, poll });
  // A demo is there from the first frame, as the page would look.
  const first = useRef(true);
  if (first.current) {
    first.current = false;
    if (demo) show(DEMO[demo] ?? DEMO.setup, false);
  }
  useEffect(() => {
    const { show, poll } = live.current;
    poll();
    const timer = setInterval(poll, 2000);
    const off = onSoundChange(() => { if (s.player) show(s.player); });
    return () => { clearInterval(timer); off(); };
  }, [s]);

  return { player: s.player, note: s.note, barArtist: s.barArtist, npOn: s.npOn, busy: s.busy, command };
}
