// Runs in Chrome on the server, never on the Hub: Spotify's official web
// player (the Web Playback SDK). It appears in Spotify as a device, and its
// sound is recorded by the server and streamed to the dashboard.

interface SpotifyPlayer {
  addListener(event: string, cb: (e: { device_id?: string; message?: string }) => void): void;
  connect(): Promise<boolean>;
}

declare global {
  interface Window {
    onSpotifyWebPlaybackSDKReady?: () => void;
    Spotify: {
      Player: new (options: {
        name: string;
        volume: number;
        getOAuthToken: (cb: (token: string) => void) => void;
      }) => SpotifyPlayer;
    };
  }
}

const name = new URLSearchParams(location.search).get('name') || 'Dashboard';
let device: string | null = null;

function report(event: string, message = '') {
  fetch('/api/engine', {
    method: 'POST',
    body: JSON.stringify({ event, device, message }),
  }).catch(() => {});
}

function token(): Promise<string | null> {
  return fetch('/api/spotify/token', { cache: 'no-store' })
    .then((r) => (r.ok ? r.json() : null))
    .then((d: { token?: string } | null) => d?.token ?? null)
    .catch(() => null);
}

// A failed start is retried from scratch: a sign-in, or a hiccup at Spotify,
// may have fixed it in the meantime.
function retry() {
  setTimeout(() => location.reload(), 30000);
}

function start() {
  token().then((t) => {
    if (!t) {   // not signed in yet
      report('waiting');
      setTimeout(start, 5000);
      return;
    }
    const s = document.createElement('script');
    s.src = 'https://sdk.scdn.co/spotify-player.js';
    s.onerror = () => {
      report('error', 'load_error: could not load the player');
      retry();
    };
    document.head.appendChild(s);
  });
}

window.onSpotifyWebPlaybackSDKReady = () => {
  const player = new window.Spotify.Player({
    name,
    volume: 1,
    getOAuthToken: (cb) => {
      token().then((t) => { if (t) cb(t); });
    },
  });
  player.addListener('ready', (e) => {
    device = e.device_id ?? null;
    report('ready');
  });
  player.addListener('not_ready', () => {
    device = null;
    report('not_ready');
  });
  for (const type of ['initialization_error', 'authentication_error', 'account_error']) {
    player.addListener(type, (e) => {
      device = null;
      report('error', `${type}: ${e?.message}`);
      retry();
    });
  }
  player.addListener('playback_error', (e) => report('playback_error', e?.message));
  player.addListener('autoplay_failed', () => report('playback_error', 'the browser held the sound back'));
  player.connect().then((ok) => {
    if (!ok) {
      report('error', 'connect_error: could not connect to Spotify');
      retry();
    }
  });
  setInterval(() => report(device ? 'alive' : 'idle'), 10000);
};

report('idle');
start();

export {};
