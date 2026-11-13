// Sound on this device. The server runs the Spotify player; this page plays
// its sound. The audio arrives as plain 16-bit stereo samples and is queued on
// the Web Audio clock, so pausing and skipping can cut it off at once.
import { heartbeat, pageStatus } from './heartbeat';
import { demo, isCast, loadedAt, params } from './environment';

const RATE = 44100, LEAD = 0.25, MAX_AHEAD = 1.5, MIN_BYTES = RATE * 4 / 20;

/** How the sound fares; goes out with the heartbeat. */
export const audio = {
  mode: 'off' as 'off' | 'webaudio' | 'element',
  state: 'off' as string,
  connected: false, kb: 0, played: 0,
  underruns: 0, drops: 0, reconnects: 0, level: 0, rate: 0,
};
(window as unknown as { dashAudio: typeof audio }).dashAudio = audio;   // readable from a debugging console

export let ctx: AudioContext | null = null;
/** Hears exactly what the speaker plays, so the picture keeps time with the sound. */
export let analyser: AnalyserNode | null = null;
let gain: GainNode | null = null, el: HTMLAudioElement | null = null, started = false;
let baseAt = 0, framesQueued = 0, lastChunk = 0, muteUntil = 0, sources: AudioBufferSourceNode[] = [];
let stash = new Uint8Array(0);

const listeners = new Set<() => void>();
/** Called whenever the sound starts or is held back. */
export function onSoundChange(fn: () => void) {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}
const changed = () => listeners.forEach((fn) => fn());

export function startSound() {
  if (started || demo) return;
  // Desktop previews stay silent unless asked (?player=1).
  if (!isCast && params.get('player') !== '1') return;
  started = true;
  const AC = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!AC || !window.ReadableStream || params.get('audio') === 'element') { startElement(); return; }
  audio.mode = 'webaudio';
  let c: AudioContext;
  try { c = new AC({ sampleRate: RATE, latencyHint: 'playback' }); } catch { c = new AC(); }
  ctx = c;
  gain = c.createGain();
  analyser = c.createAnalyser();
  analyser.fftSize = 2048;
  analyser.smoothingTimeConstant = 0.7;
  analyser.minDecibels = -85;
  analyser.maxDecibels = -22;
  gain.connect(analyser);
  analyser.connect(c.destination);
  audio.rate = c.sampleRate;
  audio.state = c.state;
  reportSoundReload();
  c.onstatechange = () => {
    audio.state = c.state;
    heartbeat('audio:' + c.state);
    changed();
  };
  readStream();
}

function readStream() {
  function again() {
    audio.connected = false;
    audio.reconnects++;
    stash = new Uint8Array(0);
    setTimeout(readStream, 2000);
  }
  fetch('/api/audio', { cache: 'no-store' }).then((r) => {
    if (!r.ok || !r.body) { again(); return; }
    audio.connected = true;
    const reader = r.body.getReader();
    // Not chained with return: an endless promise chain would never be freed.
    const next = () => {
      reader.read().then((res) => {
        if (res.done) { again(); return; }
        takeBytes(res.value);
        next();
      }, again);
    };
    next();
  }, again);
}

function takeBytes(bytes: Uint8Array) {
  audio.kb += bytes.length / 1024;
  const joined = new Uint8Array(stash.length + bytes.length);
  joined.set(stash, 0);
  joined.set(bytes, stash.length);
  stash = joined;
  if (stash.length < MIN_BYTES) return;
  const usable = stash.length - stash.length % 4;   // whole stereo frames only
  queueSamples(stash.subarray(0, usable));
  stash = stash.slice(usable);
}

function queueSamples(bytes: Uint8Array) {
  const c = ctx!;
  const frames = bytes.length / 4, quiet = performance.now() - lastChunk;
  lastChunk = performance.now();
  // Not allowed to play yet, or just told to stop: drop it and stay current.
  if (c.state !== 'running' || performance.now() < muteUntil) { baseAt = 0; return; }
  const now = c.currentTime;
  let at = baseAt + framesQueued / RATE;
  if (!baseAt || at < now + 0.02) {
    if (baseAt && quiet < 1000) audio.underruns++;
    baseAt = Math.ceil((now + LEAD) * RATE) / RATE;   // on a sample boundary
    framesQueued = 0;
    at = baseAt;
  }
  if (at - now > MAX_AHEAD) { audio.drops++; return; }   // clocks drifted: skip ahead

  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.length);
  const buffer = c.createBuffer(2, frames, RATE);
  const left = buffer.getChannelData(0), right = buffer.getChannelData(1);
  let peak = 0;
  for (let i = 0; i < frames; i++) {
    const a = view.getInt16(i * 4, true) / 32768;
    left[i] = a;
    right[i] = view.getInt16(i * 4 + 2, true) / 32768;
    if (a > peak) peak = a; else if (-a > peak) peak = -a;
  }
  audio.level = Math.round(peak * 100) / 100;
  const source = c.createBufferSource();
  source.buffer = buffer;
  source.connect(gain!);
  source.start(at);
  sources.push(source);
  if (sources.length > 40) sources.shift();
  framesQueued += frames;
  audio.played += frames / RATE;
}

/** Stop what is already queued (pause or skip pressed here): no trailing sound. */
export function cutSound(ms: number) {
  if (!ctx) return;
  muteUntil = performance.now() + ms;
  for (const source of sources) { try { source.stop(); } catch { /* not started */ } }
  sources = [];
  baseAt = 0;
}

// Fallback for a browser without Web Audio: let an <audio> element play the
// same stream as a WAV file. Simpler, but it lags a little more.
function startElement() {
  audio.mode = 'element';
  audio.state = 'suspended';
  const a = (el = new Audio());
  a.onplaying = () => { audio.state = 'running'; changed(); };
  a.onpause = () => { audio.state = 'suspended'; };
  a.onerror = a.onended = () => { audio.reconnects++; setTimeout(loadElement, 2000); };
  loadElement();
}
function loadElement() {
  el!.src = '/api/audio.wav?t=' + Date.now();
  el!.play().catch(() => {});
}

/** Browsers only start sound after a touch; every touch is a chance to. */
export function unlockSound() {
  if (ctx && ctx.state !== 'running') ctx.resume().catch(() => {});
  if (el && el.paused) el.play().catch(() => {});
}
export const soundBlocked = () => audio.mode !== 'off' && audio.state !== 'running';
export const soundRunning = () => !!ctx && ctx.state === 'running';

// The Hub holds back the sound of a freshly cast page until the screen is
// touched (as after "Hey Google": the dashboard is cast again), yet a page
// that reloads itself has been seen playing straight away. So when music is
// meant to come out here and the sound is held, reload once; at most every
// ten minutes, in case it doesn't help. /api/debug shows how it went.
const RELOAD_KEY = 'hubdash.soundReload';
let reloading = false;
export function reloadForSound() {
  if (pageStatus.touches || reloading || !isCast || Date.now() - loadedAt < 3000) return;
  try {
    if (Date.now() - (+(sessionStorage.getItem(RELOAD_KEY) ?? 0) || 0) < 600000) return;
    sessionStorage.setItem(RELOAD_KEY, String(Date.now()));
  } catch { return; }
  reloading = true;
  heartbeat('sound-reload');
  setTimeout(() => location.reload(), 300);
}
function reportSoundReload() {
  try {
    if (Date.now() - (+(sessionStorage.getItem(RELOAD_KEY) ?? 0) || 0) > 60000) return;
  } catch { return; }
  setTimeout(() => heartbeat('after-sound-reload:' + audio.state), 2000);
}
