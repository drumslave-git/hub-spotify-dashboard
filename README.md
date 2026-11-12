# Nest Hub dashboard

Keeps a custom page on a Google Nest Hub instead of its ambient screen:
time, date and weather on the left, a Spotify player on the right.

- `server.py` serves the page, fetches weather (Open-Meteo), and runs the
  keeper: whenever the Hub is idle it casts the page, and re-casts if the page
  stops reporting in. Other Cast apps are left alone; the dashboard returns
  when the Hub goes idle again.
- `spotify.py` handles the one-time Spotify sign-in and talks to the official
  Spotify Web API (what's playing, play/pause, skip, volume, resume).
- `web/index.html` is the page. It registers itself as a Cast receiver with the
  idle timeout disabled, which is what stops the Hub from closing it.

## How music works

The Hub shows one Cast app at a time, so for music to play on the Hub while
the dashboard stays on screen, the dashboard page is the player: on an HTTPS
address it runs Spotify's in-browser player and appears in Spotify as a device
(named by `SPOTIFY_PLAYER_NAME`, default "Dashboard").

- The play button resumes your last Spotify session on that device.
- If Spotify's own app starts on the Hub (for example after "Hey Google,
  play…"), the service notes the track and position, closes that app, re-casts
  the dashboard and continues the music there (`VOICE_TAKEOVER`).
- The Hub refuses to start sound from a page until the screen is touched.
  Without your own Cast app, a voice-started song therefore waits for one tap
  after the dashboard returns.
- Over plain HTTP the in-browser player cannot run; the player then only shows
  and controls music playing on other Spotify devices.

## HTTPS address

Point a hostname with a publicly trusted certificate at the service, e.g. a
reverse-proxy entry `https://hub.example.com` -> `http://<server>:8765`. The
Hub must be able to resolve the name (it may use Google's DNS, not yours) and
it only trusts public certificate authorities. Then set
`PUBLIC_URL=https://hub.example.com/`.

## Spotify setup (once)

1. At developer.spotify.com/dashboard create an app. Add the redirect URI
   `https://hub.example.com/api/spotify/callback` (your address) and tick
   "Web API" and "Web Playback SDK". Spotify Premium is required.
2. Set `SPOTIFY_CLIENT_ID` to the app's client ID. The redirect address is
   derived from `PUBLIC_URL`; set `SPOTIFY_REDIRECT_URI` only if it differs.
3. Open `https://hub.example.com/api/spotify/login` in a browser and sign in.
   The sign-in is saved in `data/spotify.json`.

Spotify only allows `https://` redirect addresses, or `http://127.0.0.1` (the
default when the dashboard's address is plain http, for running locally).

## Your own Cast app (optional, for voice-started music)

1. Register at cast.google.com/publish (one-time fee), add a new application
   of type "Custom Receiver" with the dashboard's HTTPS address as the
   receiver URL, and note its app ID.
2. In the same console add the Hub as a test device by serial number, wait
   until it shows as ready, then restart the Hub.
3. Set `CAST_APP_ID` to the app ID.

## Releases

The `VERSION` file is the release trigger.

```bash
python tools/release.py patch
```

Commit the bump and push to `main`. The Release workflow then runs the syntax
check and tests, tags `vX.Y.Z`, and publishes the image to GitHub's container
registry as `ghcr.io/<owner>/<repo>:X.Y.Z` and `:latest`. Pushes that don't
change the version publish nothing. Use `minor` / `major` for bigger changes,
or just edit the file.

The published image is private while the package is private: either make the
package public (GitHub → your profile → Packages → the package → Package
settings), or run `docker login ghcr.io` on the server with a token that has
`read:packages`.

## Deploy on a Linux server

Put `docker-compose.yml` and a `.env` (see `.env.example`) in a folder on the
server, then:

```bash
mkdir -p data && sudo chown 1000 data
```

```bash
docker compose pull && docker compose up -d
```

To update after a release, run that last command again. To build from source
instead of pulling, copy the whole repository and use
`docker compose up -d --build`.

Settings are environment variables, set in `.env`. Compose refuses to start
(and names the variable) while a required one is missing.

Required:

| Variable | Meaning |
|---|---|
| `HUB_HOST` | The Hub's IP address. Give it a fixed DHCP lease. |
| `PUBLIC_URL` | The dashboard's HTTPS address, which the Hub loads the page from. |
| `TZ` | Timezone of the clock, e.g. `Europe/London`. |
| `WEATHER_NAME`, `WEATHER_LAT`, `WEATHER_LON` | Weather location: label and coordinates. |
| `SPOTIFY_CLIENT_ID` | Your Spotify app's client ID. |

Optional:

| Variable | Default | Meaning |
|---|---|---|
| `PORT` | `8765` | Port the page is served on; the reverse proxy points here. |
| `HOUR12` | `false` | 12-hour clock. |
| `SPOTIFY_REDIRECT_URI` | `PUBLIC_URL` + `api/spotify/callback` | Where Spotify returns after sign-in. |
| `SPOTIFY_PLAYER_NAME` | `Dashboard` | Name of the dashboard's own player in Spotify. |
| `CAST_APP_ID` | public DashCast app | Your own Cast app ID. |
| `VOICE_TAKEOVER` | `true` | Move Spotify sessions started on the Hub into the dashboard. |
| `HUBDASH_IMAGE` | published `:latest` | Image to run, e.g. to pin a release. |
| `DATA_PATH` | `./data` | Folder that holds the Spotify sign-in. |

Only one instance may point at a Hub; two would keep re-casting over each other.

## Check it

- `http://<server>:8765/` shows the page in a browser. Add `?demo=playing` or
  `?demo=idle` to see the player states without Spotify.
- `http://<server>:8765/api/debug` shows the keeper state, the Hub's last
  heartbeat (including the layout it measured on its own screen), the player
  state, and recent events.
- `docker logs -f hubdash` shows the same events live.

## Local development (Windows)

```bash
python -m venv .venv
```

```bash
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

```bash
.venv\Scripts\python.exe server.py --no-keeper
```

Copy `config.example.json` to `config.json`; it supplies settings when the
environment variables are not set. `--no-keeper` serves the page without
casting it.

```bash
.venv\Scripts\python.exe -m unittest discover -s tests
```

`tools/discover.py` lists Cast devices on the network and
`tools/status.py <hub-ip>` prints what the Hub is running.
