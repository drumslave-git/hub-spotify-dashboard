// Made-up player states for ?demo=, for trying the page and for screenshots.
import type { Devices, Library, Player } from '../types';

const COVER = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='300' height='300'%3E%3Cdefs%3E%3ClinearGradient id='g' x1='0' y1='0' x2='1' y2='1'%3E%3Cstop offset='0' stop-color='%23e9a05c'/%3E%3Cstop offset='.5' stop-color='%23b8456b'/%3E%3Cstop offset='1' stop-color='%232a2f6b'/%3E%3C/linearGradient%3E%3C/defs%3E%3Crect width='300' height='300' fill='url(%23g)'/%3E%3Ccircle cx='210' cy='95' r='60' fill='%23fff' fill-opacity='.55'/%3E%3C/svg%3E";

const idle: Player = {
  configured: true, connected: true, active: false, playing: false, local_ready: true,
  last: { title: 'Quiet', artist: 'Kuoga.', art: COVER },
};

export const DEMO: Record<string, Player> = {
  playing: {
    configured: true, connected: true, active: true, playing: true, title: 'Never Catch Me (feat. Kendrick Lamar)',
    artist: 'Flying Lotus, Kendrick Lamar', device: 'Dashboard', on_dashboard: true, shuffle: true, repeat: 'track', liked: true,
    next: { title: "Dead Man's Tetris", artist: 'Flying Lotus, Captain Murphy, Snoop Dogg' },
    progress_ms: 83000, duration_ms: 234000, at: Date.now(), art: COVER,
  },
  plain: {
    configured: true, connected: true, active: true, playing: false, title: 'Weightless', artist: 'Marconi Union',
    device: 'Phone', shuffle: false, repeat: 'off', liked: false, next: null,
    progress_ms: 12000, duration_ms: 480000, at: Date.now(), art: null,
  },
  idle,
  night: idle,
  setup: { configured: false, connected: false },
};

export const DEMO_LIBRARY: Library = {
  liked: { uri: 'liked', name: 'Liked Songs', art: COVER, kind: '412 songs you liked' },
  recent: [
    { uri: 'a1', name: "You're Dead!", art: COVER, kind: 'Album' },
    { uri: 'p1', name: 'Evening cooking', art: null, kind: 'Playlist' },
  ],
  playlists: [
    { uri: 'p1', name: 'Evening cooking', art: null, kind: 'Playlist' },
    { uri: 'p2', name: 'Discover Weekly', art: COVER, kind: 'Playlist' },
    { uri: 'p3', name: 'A playlist with a very long name that will not fit', art: null, kind: 'Playlist' },
    { uri: 'p4', name: 'Focus', art: null, kind: 'Playlist' },
    { uri: 'p5', name: 'Running', art: COVER, kind: 'Playlist' },
    { uri: 'p6', name: 'Old favourites', art: null, kind: 'Playlist' },
  ],
};

export const DEMO_DEVICES: Devices = {
  devices: [
    { id: 'd1', name: 'Dashboard', type: 'computer', active: true, here: true },
    { id: 'd2', name: 'Phone', type: 'smartphone', active: false, here: false },
    { id: 'd3', name: 'Living room speaker', type: 'speaker', active: false, here: false },
  ],
};
