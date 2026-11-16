// Runs in Chrome on the server, never on the Hub: Spotify's official web
// player (the Web Playback SDK). It appears in Spotify as a device, and its
// sound is recorded by the server and streamed to the dashboard.

interface Track {
  uri: string;
  name: string;
  duration_ms?: number;
  artists?: { name: string }[];
  album?: { uri?: string; name?: string; images?: { url: string; height?: number | null }[] };
}

// What Spotify's player says it plays (its "WebPlaybackState"); null when
// the music is somewhere else.
interface PlaybackState {
  paused: boolean;
  position: number;
  duration: number;
  shuffle: boolean;
  repeat_mode: number;
  context?: { uri?: string | null } | null;
  track_window: { current_track: Track | null; next_tracks: Track[] };
}

interface SpotifyPlayer {
  addListener(event: 'player_state_changed', cb: (state: PlaybackState | null) => void): void;
  addListener(event: string, cb: (e: { device_id?: string; message?: string }) => void): void;
  connect(): Promise<boolean>;
  getCurrentState(): Promise<PlaybackState | null>;
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
// What plays here, sent along with every report: while it is known, the
// server need not ask Spotify's Web API, whose requests count against a quota.
let playback: ReturnType<typeof describe> = null;

function describe(state: PlaybackState | null) {
  const track = state?.track_window?.current_track;
  if (!state || !track) return null;
  const following = state.track_window.next_tracks?.[0];
  const names = (song: Track) => (song.artists ?? []).map((artist) => artist.name).join(', ');
  return {
    playing: !state.paused,
    position_ms: state.position,
    duration_ms: state.duration || track.duration_ms || 0,
    at: Date.now(),
    shuffle: state.shuffle,
    repeat: (['off', 'context', 'track'] as const)[state.repeat_mode] ?? 'off',
    context_uri: state.context?.uri || null,
    device_name: name,
    track: {
      uri: track.uri,
      name: track.name,
      duration_ms: state.duration || track.duration_ms || 0,
      artists: (track.artists ?? []).map((artist) => ({ name: artist.name })),
      album: { uri: track.album?.uri, name: track.album?.name, images: track.album?.images ?? [] },
    },
    next: following
      ? { title: following.name, artist: names(following) }
      : null,
  };
}

// Every reply is read to the end. Chrome keeps an unread reply's file open
// until the page's memory is cleaned up, which in this quiet page is never:
// at one report every 10 s, the container's limit of 1024 open files ran
// out after 2 h 44 min, the reports stopped, and the server restarted the
// player in the middle of the music.
function report(event: string, message = '') {
  fetch('/api/engine', {
    method: 'POST',
    body: JSON.stringify({ event, device, message, playback: device ? playback : null }),
  })
    .then((r) => r.text())
    .catch(() => {});
}

function token(): Promise<string | null> {
  return fetch('/api/spotify/token', { cache: 'no-store' })
    .then((r) => (r.ok ? r.json() : r.text().then(() => null)))
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
  // Told at once when something changes (a new song, a pause, a skip), so the
  // dashboard shows it straight away.
  player.addListener('player_state_changed', (state) => {
    playback = describe(state);
    report(device ? 'alive' : 'idle');
  });
  player.addListener('autoplay_failed', () => report('playback_error', 'the browser held the sound back'));
  player.connect().then((ok) => {
    if (!ok) {
      report('error', 'connect_error: could not connect to Spotify');
      retry();
    }
  });
  // And asked every 10 s: the position moves on, and the player says
  // nothing when the music moves to another device.
  setInterval(() => {
    player.getCurrentState()
      .then((state) => { playback = describe(state); })
      .catch(() => { playback = null; })
      .then(() => report(device ? 'alive' : 'idle'));
  }, 10000);
};

report('idle');
start();

export {};
