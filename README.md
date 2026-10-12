# WLED Matrix Weather & Pixel-Art Animations

A single-file Python script that drives a **64×32 WLED LED matrix** over **DDP** (UDP) and is controlled entirely through **MQTT**.

- **Weather screen** – weather icon, thermometer and the temperature (big integer part, small decimal).
- **Scrolling text** – publish any text to a topic and it scrolls across the matrix.
- **Nine retro pixel-art animations** – started by publishing a number (1–9). Each one is fully automatic, ends by itself and then stops streaming, so WLED goes back to its normal effect.
- **One animation at a time** – starting a new one cleanly cancels the one that is running.
- Every animation has its own block of tweakable variables at the top of the script.

> The script was developed for WLED v16 on a 64×32 HUB75 matrix, but any WLED matrix that accepts DDP should work. Change `WIDTH` / `HEIGHT` for other sizes (the animations are laid out for 64×32).

## Animations

| MQTT payload | Animation | Stops when |
|:-:|---|---|
| `1` | Mario Kart chases a big Pac-Man, who chases small Pac-Men | Everything has left the screen |
| `2` | Pac-Man plays in a maze, then a big Pac-Man runs across and the screen explodes | `PACMAN_KILLS_NODIG` ghosts caught, then the finale |
| `3` | Galaxian ship shoots the weather screen apart piece by piece | Ship has flown off |
| `4` | Space Invaders: a ship shoots descending invaders, then a big explosion | `SPACE_INVADERS_HITS_NODIG` hits |
| `5` | Breakout with an auto-playing paddle | `BREAKOUT_STENEN_WEGSPELEN` bricks destroyed |
| `6` | Pong, computer vs. computer, with deliberate misses | A player reaches `PONG_PUNTEN_NODIG` points |
| `7` | Snake (BFS pathfinding) | `SNAKE_APPELS_NODIG` apples eaten |
| `8` | Tetris with a heuristic auto-player | `TETRIS_RIJEN_NODIG` rows cleared |
| `9` | Missile Command: a battery defends the cities with predictive aiming | `MISSILE_AFGESCHOTEN_NODIG` missiles shot down |

Animation 3 uses the current weather and temperature, so publish those first.

## Requirements

- Python 3.8+
- [Pillow](https://pypi.org/project/Pillow/)
- [paho-mqtt](https://pypi.org/project/paho-mqtt/) 1.x or 2.x
- A WLED controller with a configured matrix, reachable over UDP port 4048
- An MQTT broker (e.g. Mosquitto, the Home Assistant add-on, …)

On Debian / Ubuntu / a Proxmox LXC (where `pip` is blocked by an "externally managed environment"), install everything with apt:

```bash
sudo apt install python3-pil python3-paho-mqtt
```

Otherwise: `pip install pillow paho-mqtt`.

## Installation

```bash
git clone https://github.com/wesselah/animations-hub75.git
cd <your-repo>
# put your PNG icons/sprites in ./icons  (see "Icons and sprites" below)
```

## Configuration

Open `wled_ani_text.py` and fill in the **required settings** at the top:

```python
WLED_IP = ""            # IP address of your WLED controller, e.g. "192.168.1.50"
MQTT_BROKER = ""        # IP address or hostname of your MQTT broker
MQTT_PORT = 1883
MQTT_USERNAME = ""      # leave empty if your broker allows anonymous access
MQTT_PASSWORD = ""
```

The script refuses to start until `WLED_IP` and `MQTT_BROKER` are filled in.

Everything else has working defaults:

| Section in the script | What it contains |
|---|---|
| 2. Matrix / protocol | `DDP_PORT`, `WIDTH`, `HEIGHT`, `MAX_PIXELS_PER_PACKET` |
| 3. MQTT topics | The four topics (see below) |
| 4. Icons and sprites | `ICON_DIR` and all file names |
| 5. Animation settings | One block per animation (see below) |
| 6. Weather screen layout | Positions and sizes of the icon, thermometer and temperature |

### Tweaking animations

Each animation has its own block, for example:

```python
# --- Animation 6: Pong ---
PONG_PUNTEN_NODIG = 3        # points needed to win
PONG_BAL_SNELHEID = 1.0      # start speed of the ball
PONG_FOUT_KANS = 0.45        # chance a player deliberately misses (longer/shorter game)
PONG_TOON_SCORE = True       # show the score
...
```

Common knobs you will probably want:

- **Duration** – the "…\_NODIG" / "…\_HITS\_NODIG" variable (how many points / hits / rows / apples before it stops).
- **Speed** – the "…\_INTERVAL" variables (seconds per frame, lower = faster) and the "…\_SNELHEID" variables.
- **Difficulty / randomness** – e.g. `PONG_FOUT_KANS`, `TETRIS_FOUT_KANS`, `MISSILE_DOORLAATKANS`.
- **Finale** – `…_EINDE_EXPLOSIE` toggles the big explosion at the end.
- **Colours** – all `…_KLEUR` variables are `(R, G, B)` tuples.
- **Safety** – `…_MAX_DUUR` is a hard time limit in seconds, so an animation can never run forever.

> The variable names are Dutch (`NODIG` = needed, `SNELHEID` = speed, `KLEUR` = colour, `KANS` = chance, `RIJEN` = rows, `STENEN` = bricks, `STEDEN` = cities, `APPELS` = apples, `VUUR` = fire/shoot, `TOON` = show, `EINDE` = end). Every variable has an English comment explaining what it does.

## MQTT topics

| Topic (default) | Payload | Effect |
|---|---|---|
| `home/weer/weertype` | weather type, e.g. `sunny`, `rainy`, `partlycloudy` | Sets the weather icon |
| `home/weer/temperatuur` | number in °C, e.g. `18.4` | Sets the temperature |
| `home/weer/animatie` | `1` – `9` | Starts an animation |
| `home/weer/tekst` | any text | Scrolls the text once across the matrix |

The weather screen is drawn as soon as **both** a weather type and a temperature are known, and refreshed whenever one of them changes (unless an animation is running).

Example with `mosquitto_pub`:

```bash
mosquitto_pub -h <broker> -u <user> -P <password> -t home/weer/weertype    -m rainy
mosquitto_pub -h <broker> -u <user> -P <password> -t home/weer/temperatuur -m 12.7
mosquitto_pub -h <broker> -u <user> -P <password> -t home/weer/animatie    -m 9
mosquitto_pub -h <broker> -u <user> -P <password> -t home/weer/tekst       -m "Hello world"
```

### Weather types

Home Assistant style weather states are translated automatically (see the `weer_vertalingen` table in `on_message`):

| Received | Icon key | Icon file |
|---|---|---|
| `sunny`, `clear-night`, `clear` | `clear` | `sun.png` |
| `partly-cloudy-day`, `partly-cloudy-night`, `partlycloudy` | `partlycloudy` | `cloudy-day.png` |
| `cloudy` | `cloudy` | `cloudy.png` |
| `fog` | `fog` | `fog.png` |
| `rainy`, `pouring`, `light-rain` | `rainy` | `umbrella.png` |
| `snowy` | `snowy` | `snowy.png` |
| `snowy-rainy` | `snowy-rainy` | `snowy-rainy.png` |
| `lightning` | `lightning` | `lightning.png` |
| `lightning-rainy` | `partlycloudy-lightning` | `partlycloudy-lightning.png` |
| `windy`, `breezy` | `windy` | `breezy.png` |

Add your own rows to `weer_vertalingen` and `WEER_ICOON_BESTANDEN` to support more states.

## Icons and sprites

The script loads PNG files from `ICON_DIR` (by default the `icons/` folder next to the script). Icons are scaled automatically (LANCZOS when shrinking, nearest-neighbour when enlarging so pixel art stays sharp). Use PNGs with a **transparent background**.

**This repository does not ship any images.** Several sprites depict trademarked characters (Pac-Man, Mario Kart, Space Invaders, Galaxian), so provide your own artwork. Missing files only disable the animation that needs them (a warning is printed); the weather screen and the animations that need no sprites (Breakout, Pong, Snake, Tetris, Missile Command) work without them.

| File | Used by |
|---|---|
| `sun.png`, `cloudy-day.png`, `cloudy.png`, `fog.png`, `umbrella.png`, `snowy.png`, `snowy-rainy.png`, `lightning.png`, `partlycloudy-lightning.png`, `breezy.png` | Weather screen (any size, 32×32 recommended) |
| `celsius.png` | Thermometer on the weather screen |
| `mario-kart.png` | Animation 1 |
| `pacman.png` (mouth open, **facing right**), `pacman_closed.png` | Animations 1 and 2 |
| `spook_roze.png`, `spook_cyaan.png`, `spook_blauw.png`, `spook_geel.png` | Ghosts in animation 2 |
| `galaxian.png` | Animation 3 (drawn upside down in the source image; the script rotates it) |
| `invader_paars_spin.png`, `invader_rode_krab.png`, `invader_mantis.png`, `invader_geel_hert.png`, `invader_paarse_ring.png`, `invaders_schip.png` | Animation 4 |

All file names can be changed in section 4 of the configuration.

## Running

```bash
python3 wled_ani_text.py
```

### Run as a service (systemd)

```ini
# /etc/systemd/system/wled-matrix.service
[Unit]
Description=WLED matrix weather and animations
After=network-online.target

[Service]
ExecStart=/usr/bin/python3 /opt/wled-matrix/wled_ani_text.py
WorkingDirectory=/opt/wled-matrix
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now wled-matrix
```

## How it works

- **DDP instead of the JSON API.** A full 64×32 frame is too large for WLED's JSON API (HTTP 413). DDP sends raw RGB in UDP packets of 480 pixels; the last packet carries the *push* flag so the frame is displayed.
- **Realtime timeout.** WLED returns to its normal effect when frames stop arriving (default timeout 2.5 s). Animations send a final black frame and stop streaming, and the weather screen stays visible until that timeout expires.
- **One animation at a time.** Every animation claims a new ID with `nieuwe_animatie_id()` and checks `animatie_nog_actief()` in its loop. Starting another animation (or a new weather update) invalidates the old ID, so the old thread stops at its next check.
- **Each animation runs in a daemon thread** started from the MQTT callback.

## Troubleshooting

| Problem | Solution |
|---|---|
| `Please fill in WLED_IP and MQTT_BROKER…` | Fill in the required settings at the top of the script |
| Nothing appears on the matrix | Check the IP, that UDP 4048 is reachable, and that WLED's DDP/realtime input is enabled |
| `WARNING: image '…' not found` | The sprite is missing from `ICON_DIR`; add it or ignore it if you don't use that animation |
| MQTT connection refused | Check broker address, port, username and password |
| Weather screen disappears after a few seconds | That is WLED's realtime timeout: it switches back to its own effect |

## Testing without hardware

Animations can be simulated without a matrix: replace `stuur_via_ddp` with a function that collects frames, replace `time.sleep` / `time.time` with a fake clock, call e.g. `start_pong()` and save the collected frames as an image strip.

## License

This uses an MIT license.

## 🤖 Built with Vibe Coding
A significant portion of this codebase was generated and iterated using AI tools (Claude 3.5 Sonnet).

* **AI Contribution:** ~60% of core logic and UI components.
* **Human Oversight:** Architecture design, code review, manual testing, and fine-tuning.
