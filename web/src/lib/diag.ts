// The heartbeat, the Cast receiver, and what the page learns about the device
// it runs on. The server re-casts the page when the beats stop; /api/debug
// shows the last one.
import { audio } from './audio';
import { isCast, loadedAt, params } from './env';

/** What the page is doing, kept up to date by the app; sent with each beat. */
export const diag = {
  castState: 'pending',
  touches: 0,
  stageScale: 1,
  view: 'home' as 'home' | 'playing',
  player: 'none',
  sky: {} as Record<string, unknown>,
  viz: false,
  // How regularly the clock's timer runs: a long gap means the page was frozen.
  timers: { maxGap: 0, gaps: 0 },
};

// What input this page really gets, and what else sits on top of it: both go
// out with the heartbeat. Listening on the window in the capture phase sees an
// event even if something further down would stop it.
const input: Record<string, number | string> = {};
for (const type of ['pointerdown', 'touchstart', 'mousedown', 'click', 'keydown']) {
  window.addEventListener(type, (e) => {
    input[type] = ((input[type] as number) || 0) + 1;
    const t = e.target as HTMLElement | null;
    input.last = `${type} on ${(t && (t.id || t.tagName)) || '?'}`;
  }, true);
}

/** Elements the Cast system added next to the dashboard itself. */
function foreignLayers() {
  return Array.from(document.body.children)
    .filter((el) => !/^(SCRIPT|STYLE)$/.test(el.tagName) && el.id !== 'root')
    .map((el) => {
      const box = el.getBoundingClientRect();
      return `${el.tagName.toLowerCase()} ${Math.round(box.width)}x${Math.round(box.height)} display=${getComputedStyle(el).display}`;
    });
}

// Plain requests, not keepalive ones: the Hub never gives back the 64 KiB a
// page may have in keepalive requests at once, so after about 99 beats (24.5
// minutes) every keepalive request failed, the server heard nothing and
// re-cast the page. Only a beat sent as the page goes away uses keepalive.
// `sent` reports how beats fare and how much keepalive went out.
const sent = { ok: 0, failed: 0, error: '', keepalive: 0, keepaliveBytes: 0 };
const LEAVING = /^(visibility:hidden|cast:SHUTDOWN|pagehide|freeze|sound-reload)/;

function postBeat(json: string, keepalive: boolean) {
  if (keepalive) { sent.keepalive++; sent.keepaliveBytes += json.length; }
  return fetch('/api/beat', { method: 'POST', body: json, keepalive })
    .then((r) => r.text());   // read it all: the request is over
}

const byId = (id: string) => document.getElementById(id);

function memory() {
  const m = (performance as { memory?: { usedJSHeapSize: number; jsHeapSizeLimit: number } }).memory;
  return m ? `${Math.round(m.usedJSHeapSize / 1048576)}/${Math.round(m.jsHeapSizeLimit / 1048576)} MB` : null;
}

export function beat(event?: string) {
  const home = byId('home'), side = byId('side'), time = byId('time');
  const box = time?.parentElement?.parentElement;
  const body: Record<string, unknown> = {
    up: Math.round((Date.now() - loadedAt) / 1000),
    cast: diag.castState,
    touches: diag.touches,
    input,
    layers: foreignLayers(),
    visible: document.visibilityState,
    w: innerWidth, h: innerHeight, dpr: devicePixelRatio,
    // layout check, measured on the device itself
    // (in stage pixels: the 1024 x 600 layout before scaling)
    fit: {
      scale: diag.stageScale,
      view: diag.view,
      homeOverflow: home ? home.scrollHeight - home.clientHeight : null,
      sideOverflow: side ? side.scrollHeight - side.clientHeight : null,
      timeRight: time ? Math.round(time.offsetLeft + time.scrollWidth) : null,
      timeLimit: box ? box.offsetLeft + box.offsetWidth : null,
      tint: getComputedStyle(document.documentElement).getPropertyValue('--color-tint').trim(),
    },
    player: diag.player,
    audio: { ...audio, kb: Math.round(audio.kb), played: Math.round(audio.played) },
    sky: diag.sky,
    viz: diag.viz,
    sent,
    timers: diag.timers,
    mem: memory(),
    ua: navigator.userAgent,
  };
  if (event) body.event = event;
  const json = JSON.stringify(body), keepalive = !!event && LEAVING.test(event);
  function failed(e: unknown) {
    sent.failed++;
    const first = !sent.error;
    sent.error = String((e as Error)?.message || e).slice(0, 120);
    // A keepalive beat that is refused goes again as a plain one, and the
    // first failure of all shows up on /api/debug.
    if (keepalive || first) {
      body.sent = sent;
      if (first) body.event = (event ? event + '; ' : '') + 'beat-failed: ' + sent.error;
      fetch('/api/beat', { method: 'POST', body: JSON.stringify(body) }).catch(() => {});
    }
  }
  try {
    postBeat(json, keepalive).then(() => { sent.ok++; }, failed);
  } catch (e) { failed(e); }
}

// ------------------------------------------------------------ cast receiver
// On the Hub, registering as a Cast receiver with the idle timeout disabled
// is what stops the device from dropping back to its ambient screen.
interface CastFramework {
  CastReceiverContext: { getInstance(): { addEventListener(t: unknown, cb: () => void): void; start(o: unknown): void } };
  CastReceiverOptions: new () => Record<string, unknown>;
  ui: { UiConfig: new () => Record<string, unknown> };
  system: { EventType: Record<string, unknown> };
}

function startCast() {
  if (!isCast) { diag.castState = 'not-a-cast-device'; return; }
  if (params.get('cast') === '0') { diag.castState = 'disabled'; return; }
  const s = document.createElement('script');
  s.src = 'https://www.gstatic.com/cast/sdk/libs/caf_receiver/v3/cast_receiver_framework.js';
  s.onload = () => {
    try {
      const fw = (window as unknown as { cast: { framework: CastFramework } }).cast.framework;
      const ctx = fw.CastReceiverContext.getInstance();
      const o = new fw.CastReceiverOptions();
      o.disableIdleTimeout = true;
      o.skipPlayersLoad = true;
      try {
        const ui = new fw.ui.UiConfig();
        ui.touchScreenOptimizedApp = true;
        o.uiConfig = ui;
      } catch { /* older receiver framework */ }
      for (const name of ['READY', 'SHUTDOWN', 'SENDER_CONNECTED', 'SENDER_DISCONNECTED', 'ERROR']) {
        const type = fw.system.EventType[name];
        if (type) ctx.addEventListener(type, () => beat('cast:' + name));
      }
      ctx.start(o);
      diag.castState = 'started';
    } catch (e) {
      diag.castState = 'error: ' + (e as Error)?.message;
    }
    beat('cast-init:' + diag.castState);
  };
  s.onerror = () => { diag.castState = 'sdk-load-failed'; beat('cast-init:' + diag.castState); };
  document.head.appendChild(s);
}

/** Starts the receiver, the beats and the page's lifecycle reports. */
export function startDiagnostics() {
  // On a smart display the Cast system adds its own media-control layer over
  // the page. It is invisible but would swallow every tap, and it can be
  // added (or re-shown) at any time: keep it out of the way.
  new MutationObserver(() => {
    document.querySelectorAll<HTMLElement>('touch-controls').forEach((el) => {
      el.style.setProperty('display', 'none', 'important');
    });
  }).observe(document.documentElement, { childList: true, subtree: true });
  document.addEventListener('visibilitychange', () => beat('visibility:' + document.visibilityState));
  // The browser freezing or unloading the page (the Page Lifecycle events).
  document.addEventListener('freeze', () => beat('freeze'));
  document.addEventListener('resume', () => beat('resume'));
  window.addEventListener('pagehide', () => beat('pagehide'));
  window.addEventListener('error', (e) => beat('js-error: ' + e.message));
  startCast();
  beat('loaded');
  setInterval(() => beat(), 15 * 1000);
}
