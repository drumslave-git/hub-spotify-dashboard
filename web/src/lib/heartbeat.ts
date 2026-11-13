// The heartbeat: what the page is doing and what it learns about the device it
// runs on, posted to the server every 15 seconds and on every notable event.
// The server re-casts the page when the beats stop; /api/debug shows the last.
import { audio } from './audio';
import { loadedAt } from './environment';

/** What the page is doing, kept up to date by the app; sent with each beat. */
export const pageStatus = {
  castState: 'pending',
  // The still image kept loaded as Cast media: loads that worked, last error.
  media: { loads: 0, error: '' },
  touches: 0,
  stageScale: 1,
  view: 'home' as 'home' | 'playing',
  player: 'none',
  sky: {} as Record<string, unknown>,
  visualizer: false,
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

export function heartbeat(event?: string) {
  const home = byId('home'), side = byId('side'), time = byId('time');
  const box = time?.parentElement?.parentElement;
  const body: Record<string, unknown> = {
    up: Math.round((Date.now() - loadedAt) / 1000),
    cast: pageStatus.castState,
    media: pageStatus.media,
    touches: pageStatus.touches,
    input,
    layers: foreignLayers(),
    visible: document.visibilityState,
    w: innerWidth, h: innerHeight, dpr: devicePixelRatio,
    // layout check, measured on the device itself
    // (in stage pixels: the 1024 x 600 layout before scaling)
    fit: {
      scale: pageStatus.stageScale,
      view: pageStatus.view,
      homeOverflow: home ? home.scrollHeight - home.clientHeight : null,
      sideOverflow: side ? side.scrollHeight - side.clientHeight : null,
      timeRight: time ? Math.round(time.offsetLeft + time.scrollWidth) : null,
      timeLimit: box ? box.offsetLeft + box.offsetWidth : null,
      tint: getComputedStyle(document.documentElement).getPropertyValue('--color-tint').trim(),
    },
    player: pageStatus.player,
    audio: { ...audio, kb: Math.round(audio.kb), played: Math.round(audio.played) },
    sky: pageStatus.sky,
    visualizer: pageStatus.visualizer,
    sent,
    timers: pageStatus.timers,
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

/** Sends a heartbeat now and every 15 seconds, and reports the page's
 *  lifecycle (hidden, frozen, unloading, errors) as it happens. */
export function startHeartbeat() {
  document.addEventListener('visibilitychange', () => heartbeat('visibility:' + document.visibilityState));
  // The browser freezing or unloading the page (the Page Lifecycle events).
  document.addEventListener('freeze', () => heartbeat('freeze'));
  document.addEventListener('resume', () => heartbeat('resume'));
  window.addEventListener('pagehide', () => heartbeat('pagehide'));
  window.addEventListener('error', (e) => heartbeat('js-error: ' + e.message));
  heartbeat('loaded');
  setInterval(() => heartbeat(), 15 * 1000);
}
