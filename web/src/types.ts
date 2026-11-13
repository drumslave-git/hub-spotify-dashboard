// What the server sends (server.py, spotify.py).

export interface HourForecast { time: string; temp: number; code: number; is_day: number }
export interface RainHour { time: string; chance: number; mm: number; code: number }
export interface DayForecast {
  date: string; code: number; max: number; min: number;
  sunrise?: string; sunset?: string;
}
/** The moon rising and setting again, local times like sunrise and sunset. */
export interface MoonPass { rise: string; set: string }
export interface Weather {
  code: number; is_day: number; temp: number; feels: number; wind: number; place: string;
  hours?: HourForecast[]; rain?: RainHour[]; days?: DayForecast[]; moon?: MoonPass[];
}

export interface NightHours { from: string; to: string }

export interface ServerState {
  now: number;
  timezone?: string;
  hour12?: boolean;
  night?: NightHours | null;
  version: string;
  weather: Weather | null;
}

export interface Track { title: string; artist: string; art?: string | null }

export interface LocalPlayer { state: 'off' | 'ready' | 'error' | string; message?: string }

export interface Player {
  configured?: boolean;
  connected?: boolean;
  active?: boolean;
  playing?: boolean;
  title?: string;
  artist?: string;
  art?: string | null;
  device?: string;
  on_dashboard?: boolean;
  shuffle?: boolean;
  repeat?: 'off' | 'context' | 'track';
  liked?: boolean | null;
  next?: { title: string; artist?: string } | null;
  progress_ms?: number;
  duration_ms?: number;
  at?: number;
  last?: Track | null;
  local?: LocalPlayer;
  local_ready?: boolean;
  needs_login?: boolean;
  error?: string;
}

export interface LibraryEntry { uri: string; name: string; art?: string | null; kind: string }
export interface Library { liked?: LibraryEntry | null; recent?: LibraryEntry[]; playlists?: LibraryEntry[] }
export interface Device { id: string; name: string; type?: string; active: boolean; here: boolean }
export interface Devices { devices?: Device[] }

/** The clock's settings, from the server. */
export interface Settings { tz?: string; hour12: boolean; offset: number }
