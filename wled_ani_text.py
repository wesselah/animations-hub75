"""
WLED Matrix Weather & Pixel-Art Animations
==========================================

Drives a 64x32 WLED LED matrix over DDP (UDP, port 4048) and is controlled via MQTT.

Features
--------
* Weather screen: weather icon + thermometer + temperature (large integer part,
  small decimal), fed by two MQTT topics.
* Scrolling text, sent as a plain MQTT message.
* Nine pixel-art animations that are started by publishing a number (1-9) to the
  animation topic. Every animation ends by itself and then stops streaming (black
  frame), so WLED returns to its normal effect after its realtime timeout.
* Only one animation can run at a time: starting a new one cancels the old one.

Requirements
------------
* Python 3.8+
* Pillow            (Debian/Ubuntu: apt install python3-pil)
* paho-mqtt 1.x or 2.x (Debian/Ubuntu: apt install python3-paho-mqtt)
* A folder with the PNG icons/sprites (see ICON_DIR below and the README).

Fill in WLED_IP, MQTT_BROKER (and, if your broker requires it, MQTT_USERNAME and
MQTT_PASSWORD) in the CONFIG section, then run:  python3 wled_ani_text.py
"""

import os
import sys
import socket
import struct
import time
import threading
import random
import math
import paho.mqtt.client as mqtt
from PIL import Image, ImageDraw, ImageOps

# ============================================================
# CONFIG - 1. REQUIRED SETTINGS (fill these in!)
# ============================================================

WLED_IP = ""            # IP address of your WLED controller, e.g. "192.168.1.50"
MQTT_BROKER = ""        # IP address or hostname of your MQTT broker, e.g. "192.168.1.10"
MQTT_PORT = 1883        # MQTT broker port (1883 = default, unencrypted)
MQTT_USERNAME = ""      # MQTT username. Leave empty ("") if your broker allows anonymous access
MQTT_PASSWORD = ""      # MQTT password. Leave empty ("") if your broker allows anonymous access

# ============================================================
# CONFIG - 2. MATRIX / PROTOCOL SETTINGS
# ============================================================

DDP_PORT = 4048                 # WLED's DDP (realtime) UDP port. Default is 4048, normally no need to change
WIDTH = 64                      # Matrix width in pixels
HEIGHT = 32                     # Matrix height in pixels
MAX_PIXELS_PER_PACKET = 480     # Pixels per DDP packet (480 pixels = 1440 bytes, fits in one UDP packet)

# ============================================================
# CONFIG - 3. MQTT TOPICS
# ============================================================

TOPIC_WEER_ICOON = "home/weer/weertype"      # Weather condition, e.g. "sunny", "rainy", "partlycloudy" (see translation table in on_message)
TOPIC_TEMPERATUUR = "home/weer/temperatuur"  # Temperature in degrees Celsius, e.g. "18.4"
TOPIC_ANIMATIE = "home/weer/animatie"        # Start an animation: 1 = Mario Kart chase, 2 = Pac-Man, 3 = Galaxian destruction, 4 = Space Invaders, 5 = Breakout, 6 = Pong, 7 = Snake, 8 = Tetris, 9 = Missile Command
TOPIC_TEKST = "home/weer/tekst"              # Any text published here scrolls once across the matrix

# ============================================================
# CONFIG - 4. ICONS AND SPRITES
# ============================================================

# Folder containing all PNG icons and sprites. By default an "icons" folder next to this script.
ICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")

MARIO_KART_BESTAND = "mario-kart.png"              # Sprite for animation 1 (transparent background)
PACMAN_OPEN_BESTAND = "pacman.png"                 # Pac-Man, mouth open (mouth must point RIGHT in the image)
PACMAN_DICHT_BESTAND = "pacman_closed.png"         # Pac-Man, mouth closed
GHOST_BESTANDEN = ["spook_roze.png", "spook_cyaan.png", "spook_blauw.png", "spook_geel.png"]  # Ghost sprites (one file per ghost)
GALAXIAN_BESTAND = "galaxian.png"                  # Ship for animation 3 (Galaxian destruction)
INVADER_BESTANDEN = [                              # Invader sprites for animation 4 (Space Invaders)
    "invader_paars_spin.png",
    "invader_rode_krab.png",
    "invader_mantis.png",
    "invader_geel_hert.png",
    "invader_paarse_ring.png",
]
INVADERS_SCHIP_BESTAND = "invaders_schip.png"      # Player ship for animation 4
THERMOMETER_BESTAND = "celsius.png"                # Thermometer icon on the weather screen

# ============================================================
# CONFIG - 5. ANIMATION SETTINGS
# ============================================================

# --- Animation 1: Mario Kart chases the big Pac-Man, who chases small Pac-Men ---
MARIO_JACHT_KLEINE_HOOGTE = 9      # Height in pixels of the small Pac-Men being chased
MARIO_JACHT_AANTAL_KLEIN = 3       # Number of small Pac-Men in front of the big Pac-Man
MARIO_JACHT_SNELHEID_MARIO = 1.6   # Speed in pixels/frame - Mario Kart (last in line, fastest)
MARIO_JACHT_SNELHEID_GROOT = 1.15  # Speed in pixels/frame - big Pac-Man (middle)
MARIO_JACHT_SNELHEID_KLEIN = 0.85  # Speed in pixels/frame - small Pac-Men (in front, slowest)
MARIO_JACHT_INTERVAL = 0.045       # Seconds between frames (lower = faster animation)
MARIO_JACHT_GAP_MARIO = 6          # Minimum distance (px) between Mario and the big Pac-Man (prevents overlap)
MARIO_JACHT_GAP_GROOT = 4          # Minimum distance (px) between the big Pac-Man and the small ones

# --- Animation 2: Pac-Man in a maze, then a big Pac-Man runs across and the screen explodes ---
PACMAN_MAZE_SPRITE_HOOGTE = 7      # Height in pixels of Pac-Man and the ghosts inside the maze
PACMAN_GROOT_SPRITE_HOOGTE = 26    # Height in pixels of the "big Pac-Man" that crosses the screen in the finale
PACMAN_KILLS_NODIG = 5             # Number of ghosts Pac-Man must catch before the finale starts
PACMAN_SNELHEID = 0.22             # Movement speed as a fraction of a maze cell per frame (higher = faster)
PACMAN_INTERVAL = 0.05             # Seconds between frames inside the maze
PACMAN_GROOT_STAP = 3              # Pixels per frame the big Pac-Man moves
PACMAN_GROOT_INTERVAL = 0.045      # Seconds between frames while the big Pac-Man runs
PACMAN_EXPLOSIE_STAPPEN = 13       # Number of frames of the final explosion (more = longer / smoother)
PACMAN_EXPLOSIE_INTERVAL = 0.06    # Seconds between explosion frames (higher = slower explosion)

# --- Animation 3: Galaxian destruction (the weather screen is shot apart piece by piece) ---
ANIMATIE_STAP = 1                  # Pixels per frame for the generic sprite-scroll helper
ANIMATIE_INTERVAL = 0.05           # Seconds between frames for the generic sprite-scroll helper
ANIMATIE_LOOP_DUUR = 10.0          # Maximum duration in seconds of the generic sprite-scroll helper

# --- Animation 4: Space Invaders ---
SPACE_INVADERS_HITS_NODIG = 5      # Number of invaders that must be hit before the final explosion
SPACE_INVADERS_MAX_TEGELIJK = 4    # Maximum number of invaders on screen at the same time
SPACE_INVADERS_VUUR_INTERVAL = 0.35     # Seconds between shots of the player ship
SPACE_INVADERS_INTERVAL = 0.045         # Seconds between animation frames
SPACE_INVADERS_EXPLOSIE_STAPPEN = 13    # Number of frames of the final explosion (more = smoother/longer)
SPACE_INVADERS_EXPLOSIE_INTERVAL = 0.06 # Seconds between explosion frames (higher = slower explosion)
SPACE_INVADERS_TERREIN_KLEUR = (222, 152, 80)  # Colour (R, G, B) of the ground at the bottom
# Terrain height per matrix column (64 values, in pixels), traced from a reference image
SPACE_INVADERS_TERREIN = [
    4, 3, 3, 3, 2, 2, 1, 1, 1, 1, 1, 2, 3, 3, 3, 4, 4, 3, 3, 2, 1, 1, 2, 2,
    2, 2, 2, 2, 3, 3, 3, 4, 4, 3, 3, 2, 2, 2, 3, 3, 4, 4, 4, 4, 4, 4, 4, 3,
    3, 2, 2, 2, 2, 2, 1, 1, 2, 2, 3, 3, 4, 4, 3, 3,
]

# --- Animation 5: Breakout ---
BREAKOUT_RIJEN = 2                 # Number of brick rows (max. 6)
BREAKOUT_STENEN_PER_RIJ = 8        # Number of bricks per row
BREAKOUT_STENEN_WEGSPELEN = 5      # Number of bricks that must be destroyed before the animation stops
BREAKOUT_BAL_SNELHEID = 1.1        # Ball speed in pixels per frame
BREAKOUT_PADDLE_BREEDTE = 10       # Paddle width in pixels
BREAKOUT_PADDLE_SNELHEID = 1.8     # Maximum pixels per frame the paddle can move
BREAKOUT_INTERVAL = 0.04           # Seconds between frames
BREAKOUT_MAX_DUUR = 90.0           # Safety limit in seconds
BREAKOUT_STEEN_KLEUREN = [(255, 40, 40), (255, 140, 0), (255, 220, 0), (60, 220, 60), (0, 200, 255), (160, 80, 255)]  # Brick colour per row, top to bottom
BREAKOUT_PADDLE_KLEUR = (0, 160, 255)
BREAKOUT_BAL_KLEUR = (255, 255, 255)

# --- Animation 6: Pong ---
PONG_PUNTEN_NODIG = 3              # First player to reach this many points wins; then the animation stops
PONG_BAL_SNELHEID = 1.0            # Initial ball speed (pixels per frame)
PONG_BAL_VERSNELLING = 0.04        # Ball speeds up by this much on every paddle hit (0 = constant speed)
PONG_BAL_MAX_SNELHEID = 2.2        # Upper limit for the ball speed
PONG_PADDLE_HOOGTE = 7             # Paddle height in pixels
PONG_PADDLE_SNELHEID = 1.3         # Max. pixels per frame a paddle can move (lower = more missed balls)
PONG_FOUT_KANS = 0.45              # Chance that a player deliberately misses the ball (0 = almost never, 1 = always)
PONG_TOON_SCORE = True             # Show the score at the top
PONG_SCORE_SCHAAL = 2              # Size of the score digits (1 = small, 2 = large)
PONG_TOON_MIDDENLIJN = True        # Show a dotted centre line
PONG_EINDE_FLITS = True            # Let the winner's paddle and score blink at the end
PONG_INTERVAL = 0.035              # Seconds between frames (lower = faster)
PONG_MAX_DUUR = 120.0              # Safety limit in seconds
PONG_PADDLE_KLEUR_LINKS = (0, 160, 255)
PONG_PADDLE_KLEUR_RECHTS = (255, 80, 80)
PONG_BAL_KLEUR = (255, 255, 255)
PONG_SCORE_KLEUR = (90, 90, 90)

# --- Animation 7: Snake ---
SNAKE_CEL = 2                      # Size of one grid cell in pixels (1 = fine, 2 = normal, 3 = coarse)
SNAKE_APPELS_NODIG = 8             # Animation stops after this many apples have been eaten
SNAKE_START_LENGTE = 4             # Starting length of the snake
SNAKE_GROEI_PER_APPEL = 1          # Number of cells the snake grows per apple
SNAKE_STAP_INTERVAL = 0.09         # Seconds per step (lower = faster)
SNAKE_MUREN = True                 # True: wall around the field; False: wrap around to the opposite side
SNAKE_EINDE_EXPLOSIE = True        # True: the snake explodes at the end; False: screen simply turns off
SNAKE_MAX_DUUR = 120.0             # Safety limit in seconds
SNAKE_KOP_KLEUR = (120, 255, 120)
SNAKE_LICHAAM_KLEUR = (30, 200, 60)
SNAKE_STAART_KLEUR = (10, 90, 30)
SNAKE_APPEL_KLEUR = (255, 40, 40)
SNAKE_MUUR_KLEUR = (255, 140, 0)

# --- Animation 8: Tetris ---
TETRIS_RIJEN_NODIG = 6             # Animation stops after this many cleared rows
TETRIS_BREEDTE = 10                # Playfield width in cells
TETRIS_HOOGTE = 16                 # Playfield height in cells (max. 32 / TETRIS_CEL)
TETRIS_CEL = 2                     # Size of one cell in pixels
TETRIS_VAL_INTERVAL = 0.05         # Seconds per step (lower = faster)
TETRIS_SNELLE_VAL = True           # True: pieces drop quickly once they are lined up correctly
TETRIS_FOUT_KANS = 0.15            # Chance that the player picks a worse position (0 = perfect play, higher = messier)
TETRIS_TOON_VOLGENDE = True        # Show the next piece to the right of the playfield
TETRIS_TOON_RIJEN = True           # Show the number of cleared rows to the left of the playfield
TETRIS_EINDE_EXPLOSIE = True       # True: the playfield explodes at the end; False: screen simply turns off
TETRIS_MAX_DUUR = 180.0            # Safety limit in seconds
TETRIS_KADER_KLEUR = (70, 70, 70)  # Colour of the playfield border
TETRIS_INFO_KLEUR = (200, 200, 200)  # Colour of the row counter and next-piece box
TETRIS_STUKKEN = {                 # Shape (list of cells) and colour per tetromino
    "I": ([(0, 1), (1, 1), (2, 1), (3, 1)], (0, 220, 255)),
    "O": ([(1, 0), (2, 0), (1, 1), (2, 1)], (255, 220, 0)),
    "T": ([(1, 0), (0, 1), (1, 1), (2, 1)], (170, 60, 255)),
    "S": ([(1, 0), (2, 0), (0, 1), (1, 1)], (60, 220, 60)),
    "Z": ([(0, 0), (1, 0), (1, 1), (2, 1)], (255, 50, 50)),
    "J": ([(0, 0), (0, 1), (1, 1), (2, 1)], (40, 80, 255)),
    "L": ([(2, 0), (0, 1), (1, 1), (2, 1)], (255, 140, 0)),
}

# --- Animation 9: Missile Command ---
MISSILE_STEDEN = 5                 # Number of cities at the bottom (the defence battery stands in the middle)
MISSILE_AFGESCHOTEN_NODIG = 12     # Animation stops after this many enemy missiles have been shot down
MISSILE_VIJAND_SNELHEID = 0.35     # Speed of enemy missiles (pixels per frame)
MISSILE_VERDEDIGING_SNELHEID = 1.6 # Speed of the defence missiles (pixels per frame)
MISSILE_MAX_TEGELIJK = 4           # Maximum number of enemy missiles in the air at once
MISSILE_SPAWN_MIN = 0.6            # Minimum time (seconds) between two new enemy missiles
MISSILE_SPAWN_MAX = 1.5            # Maximum time (seconds) between two new enemy missiles
MISSILE_EXPLOSIE_RADIUS = 5        # Maximum radius in pixels of a defence explosion
MISSILE_EXPLOSIE_DUUR = 14         # Number of frames an explosion lasts
MISSILE_DOORLAATKANS = 0.15        # Chance that the defence deliberately ignores a missile (0 = shoot everything, higher = more city damage)
MISSILE_MAX_VERDEDIGING = 2        # Maximum number of defence missiles in flight at once
MISSILE_VUUR_INTERVAL = 0.5        # Minimum time (seconds) between two shots of the battery
MISSILE_TOON_SCORE = True          # Show the number of shot-down missiles at the top left
MISSILE_EINDE_EXPLOSIE = True      # True: big fireball at the end; False: screen simply turns off
MISSILE_INTERVAL = 0.04            # Seconds between frames (lower = faster)
MISSILE_MAX_DUUR = 120.0           # Safety limit in seconds
MISSILE_STAD_KLEUR = (60, 140, 255)
MISSILE_GROND_KLEUR = (200, 110, 20)
MISSILE_VIJAND_SPOOR_KLEUR = (110, 20, 20)
MISSILE_VIJAND_KOP_KLEUR = (255, 70, 70)
MISSILE_VERDEDIGING_SPOOR_KLEUR = (20, 90, 110)
MISSILE_SCORE_KLEUR = (110, 110, 110)

# --- Scrolling text (MQTT topic TOPIC_TEKST) ---
SCROLL_INTERVAL = 0.03             # Seconds per scroll step (lower = faster)
TEKST_KLEUR = (255, 255, 0)        # Text colour (R, G, B)

# ============================================================
# CONFIG - 6. WEATHER SCREEN LAYOUT
# ============================================================

ACHTERGROND = (0, 0, 0)            # Background colour
TEMP_TEKST_KLEUR = (255, 255, 255) # Temperature text colour

WEERICOON_GROOTTE = 28             # Weather icon size in pixels (square)
WEERICOON_X, WEERICOON_Y = 2, 2    # Weather icon position (top-left corner)

THERMO_GROOTTE = 14                # Thermometer icon size in pixels (square)
THERMO_X, THERMO_Y = 41, 1         # Thermometer icon position

TEMP_TEKST_X, TEMP_TEKST_Y = 34, 17  # Position of the temperature text
TEMP_TEKST_FACTOR = 2              # Scale factor of the large (integer) digits
TEMP_TEKST_SPATIERING = 2          # Spacing in pixels between the large digits

# ============================================================
# Weather type -> icon file name mapping
# ============================================================

WEER_ICOON_BESTANDEN = {
    "clear": "sun.png",
    "partlycloudy": "cloudy-day.png",
    "cloudy": "cloudy.png",
    "fog": "fog.png",
    "light-rain": "umbrella.png",
    "rainy": "umbrella.png",
    "snowy": "snowy.png",
    "snowy-rainy": "snowy-rainy.png",
    "lightning": "lightning.png",
    "partlycloudy-lightning": "partlycloudy-lightning.png",
    "windy": "breezy.png",
}

THERMOMETER_BESTAND = "celsius.png"

# ============================================================
# Loading and scaling icons and sprites
# ============================================================

def laad_weericonen():
    """Load all weather icons from ICON_DIR, scaled to WEERICOON_GROOTTE. Returns {weather_key: Image}."""
    iconen = {}
    for code, bestand in WEER_ICOON_BESTANDEN.items():
        pad = os.path.join(ICON_DIR, bestand)
        if not os.path.exists(pad):
            continue
        img = Image.open(pad).convert("RGBA")
        img = img.resize((WEERICOON_GROOTTE, WEERICOON_GROOTTE), Image.LANCZOS)
        iconen[code] = img
    return iconen

def laad_thermometer_icoon():
    """Load the thermometer icon scaled to THERMO_GROOTTE (None if the file is missing)."""
    pad = os.path.join(ICON_DIR, THERMOMETER_BESTAND)
    if not os.path.exists(pad):
        return None
    img = Image.open(pad).convert("RGBA")
    return img.resize((THERMO_GROOTTE, THERMO_GROOTTE), Image.LANCZOS)

def laad_sprite(bestandsnaam, doel_hoogte):
    """Load a PNG sprite from ICON_DIR and scale it to the target height, keeping the aspect ratio."""
    pad = os.path.join(ICON_DIR, bestandsnaam)
    if not os.path.exists(pad):
        print(f"WARNING: image '{bestandsnaam}' not found at {pad}")
        return None
    img = Image.open(pad).convert("RGBA")
    schaal = doel_hoogte / img.height
    doel_breedte = max(1, round(img.width * schaal))
    # When enlarging, LANCZOS produces a blur/halo around pixel art; NEAREST keeps the
    # sprite sharp. When shrinking, LANCZOS still gives the best quality.
    methode = Image.NEAREST if doel_hoogte >= img.height else Image.LANCZOS
    return img.resize((doel_breedte, doel_hoogte), methode)

# Load everything once at start-up
WEER_ICONEN_CACHE = laad_weericonen()
THERMOMETER_ICOON_CACHE = laad_thermometer_icoon()

MARIO_SPRITE = laad_sprite(MARIO_KART_BESTAND, doel_hoogte=26)
PACMAN_FRAMES = [
    laad_sprite(PACMAN_OPEN_BESTAND, doel_hoogte=19),
    laad_sprite(PACMAN_DICHT_BESTAND, doel_hoogte=19)
]
if PACMAN_FRAMES[1] is None:
    PACMAN_FRAMES[1] = PACMAN_FRAMES[0]

# Pac-Man and ghosts at maze scale (smaller than the scroll animation above)
PACMAN_MAZE_FRAMES = [
    laad_sprite(PACMAN_OPEN_BESTAND, doel_hoogte=PACMAN_MAZE_SPRITE_HOOGTE),
    laad_sprite(PACMAN_DICHT_BESTAND, doel_hoogte=PACMAN_MAZE_SPRITE_HOOGTE),
]
if PACMAN_MAZE_FRAMES[1] is None:
    PACMAN_MAZE_FRAMES[1] = PACMAN_MAZE_FRAMES[0]

GHOST_SPRITES = [s for s in (laad_sprite(b, doel_hoogte=PACMAN_MAZE_SPRITE_HOOGTE) for b in GHOST_BESTANDEN) if s is not None]

# Big Pac-Man for the finale run across the screen
PACMAN_GROOT_FRAMES = [
    laad_sprite(PACMAN_OPEN_BESTAND, doel_hoogte=PACMAN_GROOT_SPRITE_HOOGTE),
    laad_sprite(PACMAN_DICHT_BESTAND, doel_hoogte=PACMAN_GROOT_SPRITE_HOOGTE),
]
if PACMAN_GROOT_FRAMES[1] is None:
    PACMAN_GROOT_FRAMES[1] = PACMAN_GROOT_FRAMES[0]

# Small Pac-Men for the chase animation (Mario -> big Pac-Man -> small Pac-Men)
PACMAN_KLEIN_FRAMES = [
    laad_sprite(PACMAN_OPEN_BESTAND, doel_hoogte=MARIO_JACHT_KLEINE_HOOGTE),
    laad_sprite(PACMAN_DICHT_BESTAND, doel_hoogte=MARIO_JACHT_KLEINE_HOOGTE),
]
if PACMAN_KLEIN_FRAMES[1] is None:
    PACMAN_KLEIN_FRAMES[1] = PACMAN_KLEIN_FRAMES[0]

# Load the Galaxian sprite and turn it upright
GALAXIAN_SPRITE = laad_sprite(GALAXIAN_BESTAND, doel_hoogte=9)
if GALAXIAN_SPRITE:
    GALAXIAN_SPRITE = GALAXIAN_SPRITE.rotate(180, expand=True)

# Space Invaders sprites
INVADERS_SPRITES = [s for s in (laad_sprite(b, doel_hoogte=8) for b in INVADER_BESTANDEN) if s is not None]
INVADERS_SCHIP_SPRITE = laad_sprite(INVADERS_SCHIP_BESTAND, doel_hoogte=11)

# ============================================================
# Fonts & text functions
# ============================================================

FONTS_5X7 = {
    "A": [".XXX.", "X...X", "X...X", "XXXXX", "X...X", "X...X", "X...X"],
    "B": ["XXXX.", "X...X", "X...X", "XXXX.", "X...X", "X...X", "XXXX."],
    "C": [".XXXX", "X....", "X....", "X....", "X....", "X....", ".XXXX"],
    "D": ["XXXX.", "X...X", "X...X", "X...X", "X...X", "X...X", "XXXX."],
    "E": ["XXXXX", "X....", "X....", "XXXX.", "X....", "X....", "XXXXX"],
    "F": ["XXXXX", "X....", "X....", "XXXX.", "X....", "X....", "X...."],
    "G": [".XXXX", "X....", "X....", "X.XXX", "X...X", "X...X", ".XXXX"],
    "H": ["X...X", "X...X", "X...X", "XXXXX", "X...X", "X...X", "X...X"],
    "I": ["XXXXX", "..X..", "..X..", "..X..", "..X..", "..X..", "XXXXX"],
    "J": ["..XXX", "....X", "....X", "....X", "X...X", "X...X", ".XXX."],
    "K": ["X...X", "X..X.", "X.X..", "X....", "X.X..", "X..X.", "X...X"],
    "L": ["X....", "X....", "X....", "X....", "X....", "X....", "XXXXX"],
    "M": ["X...X", "X.X.X", "X.X.X", "X...X", "X...X", "X...X", "X...X"],
    "N": ["X...X", "X...X", "X.X.X", "X...X", "X...X", "X...X", "X...X"],
    "O": [".XXX.", "X...X", "X...X", "X...X", "X...X", "X...X", ".XXX."],
    "P": ["XXXX.", "X...X", "X...X", "XXXX.", "X....", "X....", "X...."],
    "Q": [".XXX.", "X...X", "X...X", "X...X", "X.X.X", "X..X.", "...X."],
    "R": ["XXXX.", "X...X", "X...X", "XXXX.", "X.X..", "X..X.", "X...X"],
    "S": [".XXXX", "X....", "X....", ".XXX.", "....X", "....X", "XXXX."],
    "T": ["XXXXX", "..X..", "..X..", "..X..", "..X..", "..X..", "..X.."],
    "U": ["X...X", "X...X", "X...X", "X...X", "X...X", "X...X", ".XXX."],
    "V": ["X...X", "X...X", "X...X", "X...X", "X...X", ".X.X.", "..X.."],
    "W": ["X...X", "X...X", "X...X", "X.X.X", "X.X.X", "X.X.X", "X...X"],
    "X": ["X...X", "X...X", ".X.X.", "..X..", ".X.X.", "X...X", "X...X"],
    "Y": ["X...X", "X...X", ".X.X.", "..X..", "..X..", "..X..", "..X.."],
    "Z": ["XXXXX", "....X", "...X.", "..X..", ".X...", "X....", "XXXXX"],
    "a": [".....", ".XXX.", "....X", ".XXXX", "X...X", "X...X", ".XXXX"],
    "b": ["X....", "X....", "XXXX.", "X...X", "X...X", "X...X", "XXXX."],
    "c": [".....", ".XXXX", "X....", "X....", "X....", "X....", ".XXXX"],
    "d": ["....X", "....X", ".XXXX", "X...X", "X...X", "X...X", ".XXXX"],
    "e": [".....", ".XXX.", "X...X", "XXXXX", "X....", "X....", ".XXX."],
    "f": ["..X..", ".X...", "XXXX.", ".X...", ".X...", ".X...", ".X..."],
    "g": [".....", ".XXXX", "X...X", "X...X", ".XXXX", "....X", ".XXX."],
    "h": ["X....", "X....", "XXXX.", "X...X", "X...X", "X...X", "X...X"],
    "i": ["..X..", ".....", "XXXX.", "..X..", "..X..", "..X..", "XXXXX"],
    "j": ["...X.", ".....", "...X.", "...X.", "...X.", "X..X.", ".X..."],
    "k": ["X....", "X....", "X..X.", "X.X..", "X....", "X.X..", "X..X."],
    "l": ["..X..", "..X..", "..X..", "..X..", "..X..", "..X..", ".XXX."],
    "m": [".....", "XXXXX", "X.X.X", "X.X.X", "X.X.X", "X...X", "X...X"],
    "n": [".....", "XXXX.", "X...X", "X...X", "X...X", "X...X", "X...X"],
    "o": [".....", ".XXX.", "X...X", "X...X", "X...X", "X...X", ".XXX."],
    "p": [".....", "XXXX.", "X...X", "X...X", "XXXX.", "X....", "X...."],
    "q": [".....", ".XXXX", "X...X", "X...X", ".XXXX", "....X", "....X"],
    "r": [".....", "XXXX.", "X....", "X....", "X....", "X....", "X...."],
    "s": [".....", ".XXXX", "X....", ".XXX.", "....X", "....X", "XXXX."],
    "t": ["..X..", "XXXXX", "..X..", "..X..", "..X..", "..X..", "...X."],
    "u": [".....", "X...X", "X...X", "X...X", "X...X", "X...X", ".XXXX"],
    "v": [".....", "X...X", "X...X", "X...X", ".X.X.", ".X.X.", "..X.."],
    "w": [".....", "X...X", "X...X", "X.X.X", "X.X.X", "XX.XX", "X...X"],
    "x": [".....", "X...X", ".X.X.", "..X..", ".X.X.", "X...X", "X...X"],
    "y": [".....", "X...X", "X...X", "X...X", ".XXXX", "....X", ".XXX."],
    "z": [".....", "XXXXX", "...X.", "..X..", ".X...", "X....", "XXXXX"],
    "0": [".XXX.", "X...X", "X...X", "X.X.X", "X...X", "X...X", ".XXX."],
    "1": ["..X..", ".XX..", "..X..", "..X..", "..X..", "..X..", "XXXXX"],
    "2": [".XXX.", "X...X", "....X", "...X.", "..X..", ".X...", "XXXXX"],
    "3": [".XXX.", "X...X", "....X", "..XX.", "....X", "X...X", ".XXX."],
    "4": ["X....", "X...X", "X...X", "XXXXX", "....X", "....X", "....X"],
    "5": ["XXXXX", "X....", "XXXX.", "....X", "....X", "X...X", ".XXX."],
    "6": [".XXX.", "X....", "X....", "XXXX.", "X...X", "X...X", ".XXX."],
    "7": ["XXXXX", "....X", "...X.", "..X..", ".X...", "X....", "X...."],
    "8": [".XXX.", "X...X", "X...X", ".XXX.", "X...X", "X...X", ".XXX."],
    "9": [".XXX.", "X...X", "X...X", ".XXXX", "....X", "....X", ".XXX."],
    " ": [".....", ".....", ".....", ".....", ".....", ".....", "....."],
    "-": [".....", ".....", ".....", "XXXXX", ".....", ".....", "....."],
    "°": [".XX..", "X..X.", ".XX..", ".....", ".....", ".....", "....."],
    ".": [".....", ".....", ".....", ".....", ".....", "..X..", "..X.."],
    "!": ["..X..", "..X..", "..X..", "..X..", "..X..", ".....", "..X.."],
    ",": [".....", ".....", ".....", ".....", ".....", "..X..", ".X..."],
    ":": [".....", "..X..", "..X..", ".....", "..X..", "..X..", "....."],
}

CIJFERS_COMPACT = {
    "0": ["XXX", "X.X", "X.X", "X.X", "XXX"],
    "1": [".X.", "XX.", ".X.", ".X.", "XXX"],
    "2": ["XXX", "..X", "XXX", "X..", "XXX"],
    "3": ["XXX", "..X", "XXX", "..X", "XXX"],
    "4": ["X.X", "X.X", "XXX", "..X", "..X"],
    "5": ["XXX", "X..", "XXX", "..X", "XXX"],
    "6": ["XXX", "X..", "XXX", "X.X", "XXX"],
    "7": ["XXX", "..X", "..X", "..X", "..X"],
    "8": ["XXX", "X.X", "XXX", "X.X", "XXX"],
    "9": ["XXX", "X.X", "XXX", "..X", "XXX"],
    "-": ["...", "...", "XXX", "...", "..."],
    "°": [".X.", "X.X", ".X.", "...", "..."],
    ".": ["...", "...", "...", "...", ".X."],
}

def maak_canvas(achtergrond=ACHTERGROND):
    """Create a new empty WIDTHxHEIGHT RGB canvas."""
    return Image.new("RGB", (WIDTH, HEIGHT), achtergrond)

def zet_pixel(canvas, x, y, kleur):
    """Set one pixel; coordinates outside the matrix are ignored."""
    if 0 <= x < WIDTH and 0 <= y < HEIGHT:
        canvas.putpixel((x, y), kleur)

def plak_icoon(canvas, icoon_img, x_offset, y_offset):
    """Paste an icon/sprite onto the canvas, respecting its alpha channel (transparency)."""
    if icoon_img is None:
        return
    if icoon_img.mode == 'RGBA':
        canvas.paste(icoon_img, (x_offset, y_offset), icoon_img)
    else:
        canvas.paste(icoon_img, (x_offset, y_offset))

def teken_tekst_flexibel(canvas, tekst, x_offset, y_offset, kleur, factor=1, spatiëring=1):
    """Draw text with the 5x7 font, scaled by 'factor'. Returns the x position after the last character."""
    x = x_offset
    for teken in tekst:
        patroon = FONTS_5X7.get(teken, FONTS_5X7.get(teken.upper(), FONTS_5X7[" "]))
        if patroon:
            for ry, rij in enumerate(patroon):
                for rx, ch in enumerate(rij):
                    if ch == 'X':
                        for fy in range(factor):
                            for fx in range(factor):
                                zet_pixel(canvas, x + rx*factor + fx, y_offset + ry*factor + fy, kleur)
        x += 5*factor + spatiëring
    return x

def teken_temperatuur_compact(canvas, tekst, x_offset, y_offset, kleur, factor=1, spatiëring=1):
    """Draw digits (and '-', '.', degree sign) with the compact 3x5 font, scaled by 'factor'.
    Returns the x position after the last character."""
    x = x_offset
    for teken in tekst:
        patroon = CIJFERS_COMPACT.get(teken, CIJFERS_COMPACT.get(" "))
        if patroon:
            for ry, rij in enumerate(patroon):
                for rx, ch in enumerate(rij):
                    if ch == 'X':
                        for fy in range(factor):
                            for fx in range(factor):
                                zet_pixel(canvas, x + rx*factor + fx, y_offset + ry*factor + fy, kleur)
        x += 3*factor + spatiëring
    return x

def teken_temperatuur(canvas, temperatuur_celsius, x_offset, y_offset, kleur):
    """Draw a temperature with large integer digits and a small decimal, e.g. 18.4 degrees."""
    afgerond = round(temperatuur_celsius, 1)
    negatief = afgerond < 0
    absoluut = abs(afgerond)
    geheel = int(absoluut)
    decimaal = int(round((absoluut - geheel) * 10))
    if decimaal == 10:
        geheel += 1
        decimaal = 0

    groot_tekst = f"{'-' if negatief else ''}{geheel}"
    x = teken_temperatuur_compact(canvas, groot_tekst, x_offset, y_offset, kleur, factor=TEMP_TEKST_FACTOR, spatiëring=TEMP_TEKST_SPATIERING)
    klein_tekst = f".{decimaal}\u00b0"
    teken_temperatuur_compact(canvas, klein_tekst, x + 1, y_offset, kleur, factor=1, spatiëring=1)

# ============================================================
# DDP communication
# ============================================================

def canvas_naar_bytes(canvas):
    """Convert the canvas to raw RGB bytes (3 bytes per pixel, row by row)."""
    return canvas.convert("RGB").tobytes()

def stuur_via_ddp(canvas):
    """Send the canvas to the WLED controller as DDP packets over UDP.
    The last packet carries the 'push' flag (0x41) so WLED displays the frame.
    WLED falls back to its normal effect after its realtime timeout (2.5 s by default)
    once no more frames arrive."""
    data = canvas_naar_bytes(canvas)
    total_len = len(data)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    offset = 0
    while offset < total_len:
        chunk = data[offset: offset + MAX_PIXELS_PER_PACKET * 3]
        is_last = (offset + len(chunk)) >= total_len
        flags = 0x41 if is_last else 0x40
        header = struct.pack('>BBBBIH', flags, 0, 0x01, 1, offset, len(chunk))
        sock.sendto(header + chunk, (WLED_IP, DDP_PORT))
        offset += len(chunk)
    sock.close()

# ============================================================
# Animation control & animations (each runs in its own thread)
# ============================================================

# Shared animation state: the Event is set while an animation runs; the ID counter + lock
# guarantee that only the most recently started animation may draw.
animatie_actief = threading.Event()
animatie_lock = threading.Lock()
huidige_animatie_id = 0

def nieuwe_animatie_id():
    """Claim a new, unique animation ID and make it the 'current' one immediately.
    Any animation still running with an older ID stops itself at its next check
    (animatie_nog_actief), so two animations can never send frames to the
    matrix at the same time."""
    global huidige_animatie_id
    with animatie_lock:
        huidige_animatie_id += 1
        animatie_actief.set()
        return huidige_animatie_id

def animatie_nog_actief(mijn_id):
    """Return True as long as no newer animation has been started that took over from this one."""
    return huidige_animatie_id == mijn_id

def animatie_klaar(mijn_id):
    """Report that animation 'mijn_id' has finished. The 'active' flag is only
    cleared if no newer animation has been started in the meantime."""
    with animatie_lock:
        if huidige_animatie_id == mijn_id:
            animatie_actief.clear()

def toon_weerscherm(weer_code, temperatuur_celsius):
    """Show the weather screen (icon + thermometer + temperature). Cancels any running animation."""
    mijn_id = nieuwe_animatie_id()  # immediately cancels any animation that is still running

    canvas = maak_canvas()
    weer_icoon = WEER_ICONEN_CACHE.get(weer_code)
    plak_icoon(canvas, weer_icoon, WEERICOON_X, WEERICOON_Y)
    plak_icoon(canvas, THERMOMETER_ICOON_CACHE, THERMO_X, THERMO_Y)
    teken_temperatuur(canvas, temperatuur_celsius, TEMP_TEKST_X, TEMP_TEKST_Y, TEMP_TEKST_KLEUR)

    stuur_via_ddp(canvas)
    animatie_klaar(mijn_id)

def start_animatie_loop(sprites, richting="links"):
    """Generic helper: scroll one or more sprites across the matrix for ANIMATIE_LOOP_DUUR seconds."""
    if not sprites or any(s is None for s in sprites if isinstance(sprites, list)):
        print("Cannot start animation: sprite(s) missing.")
        return

    if not isinstance(sprites, list):
        sprites = [sprites]

    mijn_id = nieuwe_animatie_id()

    sprite_breedte = sprites[0].width
    sprite_hoogte = sprites[0].height
    y_pos = (HEIGHT - sprite_hoogte) // 2

    if richting == "links":
        x_pos = WIDTH
        stap = -ANIMATIE_STAP
    else:
        x_pos = -sprite_breedte
        stap = ANIMATIE_STAP

    start_tijd = time.time()
    frame_index = 0
    stap_teller = 0

    while animatie_nog_actief(mijn_id) and (time.time() - start_tijd < ANIMATIE_LOOP_DUUR):
        huidige_sprite = sprites[frame_index % len(sprites)]

        canvas = maak_canvas()
        plak_icoon(canvas, huidige_sprite, int(x_pos), y_pos)
        stuur_via_ddp(canvas)

        x_pos += stap
        stap_teller += 1

        if stap_teller % 3 == 0:
            frame_index += 1

        if richting == "links" and x_pos < -sprite_breedte:
            x_pos = WIDTH
        elif richting == "rechts" and x_pos > WIDTH:
            x_pos = -sprite_breedte

        time.sleep(ANIMATIE_INTERVAL)

    animatie_klaar(mijn_id)

def start_mario_jacht():
    """Animation 1: Mario Kart chases the big Pac-Man, who in turn chases a group
    of small Pac-Men. Everything moves in the same direction (right to left across
    the matrix), with the chaser always slightly faster than whoever it chases.
    Minimum gaps prevent the sprites from overlapping."""
    mijn_id = nieuwe_animatie_id()

    if MARIO_SPRITE is None or PACMAN_GROOT_FRAMES[0] is None or PACMAN_KLEIN_FRAMES[0] is None:
        print("Cannot start Mario chase: sprite(s) missing.")
        animatie_klaar(mijn_id)
        return

    entiteiten = []

    # Small Pac-Men: in front, slightly spread in height and start position
    klein_y_posities = [1, (HEIGHT - MARIO_JACHT_KLEINE_HOOGTE) // 2, HEIGHT - MARIO_JACHT_KLEINE_HOOGTE - 1]
    for i in range(MARIO_JACHT_AANTAL_KLEIN):
        entiteiten.append({
            "x": float(WIDTH - i * 6),
            "y": klein_y_posities[i % len(klein_y_posities)],
            "snelheid": MARIO_JACHT_SNELHEID_KLEIN,
            "frames": PACMAN_KLEIN_FRAMES,
            "mond_open": True,
            "mond_teller": 0,
        })

    # Big Pac-Man: in the middle, chases the small ones
    entiteiten.append({
        "x": float(WIDTH + 14),
        "y": (HEIGHT - PACMAN_GROOT_FRAMES[0].height) // 2,
        "snelheid": MARIO_JACHT_SNELHEID_GROOT,
        "frames": PACMAN_GROOT_FRAMES,
        "mond_open": True,
        "mond_teller": 0,
    })

    # Mario Kart: at the back, fastest, chases the big Pac-Man
    mario_entiteit = {
        "x": float(WIDTH + 34),
        "y": (HEIGHT - MARIO_SPRITE.height) // 2,
        "snelheid": MARIO_JACHT_SNELHEID_MARIO,
        "sprite": MARIO_SPRITE,
    }

    kleine_pacmannen = entiteiten[:MARIO_JACHT_AANTAL_KLEIN]
    grote_pacman = entiteiten[MARIO_JACHT_AANTAL_KLEIN]

    while animatie_nog_actief(mijn_id):
        for e in entiteiten:
            e["x"] -= e["snelheid"]
            e["mond_teller"] += 1
            if e["mond_teller"] % 3 == 0:
                e["mond_open"] = not e["mond_open"]
        mario_entiteit["x"] -= mario_entiteit["snelheid"]

        # Enforce a minimum following distance: prevents overlap regardless of the
        # configured speeds, so it stays a clear chase instead of the chaser
        # "shooting" straight through the sprite in front.
        achterkant_kleintjes = max(k["x"] + k["frames"][0].width for k in kleine_pacmannen)
        minimaal_groot_x = achterkant_kleintjes + MARIO_JACHT_GAP_GROOT
        if grote_pacman["x"] < minimaal_groot_x:
            grote_pacman["x"] = minimaal_groot_x

        minimaal_mario_x = grote_pacman["x"] + grote_pacman["frames"][0].width + MARIO_JACHT_GAP_MARIO
        if mario_entiteit["x"] < minimaal_mario_x:
            mario_entiteit["x"] = minimaal_mario_x

        # Stop as soon as everyone has left the matrix on the left
        if (mario_entiteit["x"] + mario_entiteit["sprite"].width < 0 and
                all(e["x"] + e["frames"][0].width < 0 for e in entiteiten)):
            break

        canvas = maak_canvas()
        for e in entiteiten:
            sprite = e["frames"][0] if e["mond_open"] else e["frames"][1]
            plak_icoon(canvas, sprite, int(e["x"]), int(e["y"]))
        plak_icoon(canvas, mario_entiteit["sprite"], int(mario_entiteit["x"]), int(mario_entiteit["y"]))

        stuur_via_ddp(canvas)
        time.sleep(MARIO_JACHT_INTERVAL)

    leeg_canvas = maak_canvas()
    stuur_via_ddp(leeg_canvas)
    animatie_klaar(mijn_id)
    print("Mario chase animation finished.")

def start_breakout():
    """Animation 5: Breakout. An automatically played paddle keeps the ball in play
    and breaks bricks. As soon as BREAKOUT_STENEN_WEGSPELEN bricks are gone the
    animation stops and so does the streaming."""
    mijn_id = nieuwe_animatie_id()

    rijen = max(1, min(6, BREAKOUT_RIJEN))
    per_rij = max(1, BREAKOUT_STENEN_PER_RIJ)
    totaal_stenen = rijen * per_rij
    doel = max(1, min(BREAKOUT_STENEN_WEGSPELEN, totaal_stenen))

    steen_b = WIDTH // per_rij          # width of a brick cell (incl. 1px gap)
    marge_links = (WIDTH - steen_b * per_rij) // 2
    steen_top = 2
    steen_rij_h = 4                     # 3px brick + 1px gap
    stenen = {(r, c) for r in range(rijen) for c in range(per_rij)}
    flitsen = {}                        # (r, c) -> remaining frames of the "broken" flash

    paddle_y = HEIGHT - 3
    paddle_b = BREAKOUT_PADDLE_BREEDTE
    paddle_x = (WIDTH - paddle_b) / 2.0
    paddle_doel_offset = 0.0

    def nieuwe_bal():
        hoek_vx = random.choice([-0.6, -0.4, 0.4, 0.6])
        vy = -math.sqrt(max(0.1, BREAKOUT_BAL_SNELHEID ** 2 - hoek_vx ** 2))
        return {"x": paddle_x + paddle_b / 2.0, "y": float(paddle_y - 3), "vx": hoek_vx, "vy": vy}

    bal = nieuwe_bal()
    weggespeeld = 0
    start = time.time()

    while animatie_nog_actief(mijn_id) and weggespeeld < doel:
        if time.time() - start > BREAKOUT_MAX_DUUR:
            break

        # Move the ball in small sub-steps so it never flies straight through a brick or the paddle
        stappen = max(1, int(math.ceil(BREAKOUT_BAL_SNELHEID / 0.4)))
        for _ in range(stappen):
            bal["x"] += bal["vx"] / stappen
            bal["y"] += bal["vy"] / stappen

            # Side walls and top
            if bal["x"] < 0:
                bal["x"] = 0.0
                bal["vx"] = abs(bal["vx"])
            elif bal["x"] > WIDTH - 2:
                bal["x"] = float(WIDTH - 2)
                bal["vx"] = -abs(bal["vx"])
            if bal["y"] < 0:
                bal["y"] = 0.0
                bal["vy"] = abs(bal["vy"])

            # Bricks
            kol = int((bal["x"] + 1 - marge_links) // steen_b)
            rij = int((bal["y"] + 1 - steen_top) // steen_rij_h)
            if (rij, kol) in stenen:
                stenen.discard((rij, kol))
                flitsen[(rij, kol)] = 3
                weggespeeld += 1
                bal["vy"] = -bal["vy"]
                if weggespeeld >= doel:
                    break

            # Paddle
            if bal["vy"] > 0 and bal["y"] + 2 >= paddle_y and bal["y"] <= paddle_y + 1:
                if paddle_x - 1 <= bal["x"] + 1 and bal["x"] <= paddle_x + paddle_b:
                    rel = ((bal["x"] + 1) - (paddle_x + paddle_b / 2.0)) / (paddle_b / 2.0)
                    rel = max(-1.0, min(1.0, rel))
                    bal["vx"] = rel * BREAKOUT_BAL_SNELHEID * 0.8
                    if abs(bal["vx"]) < 0.25:
                        bal["vx"] = random.choice([-0.25, 0.25])
                    bal["vy"] = -math.sqrt(max(0.1, BREAKOUT_BAL_SNELHEID ** 2 - bal["vx"] ** 2))
                    bal["y"] = float(paddle_y - 2)
                    paddle_doel_offset = random.uniform(-3.0, 3.0)  # next time, don't hit exactly in the middle

        # Ball missed? Then launch a new ball from the paddle
        if bal["y"] > HEIGHT:
            bal = nieuwe_bal()

        if weggespeeld >= doel:
            break

        # Paddle follows the ball (limited speed, so it looks natural)
        gewenst = bal["x"] + 1 - paddle_b / 2.0 + paddle_doel_offset
        verschil = gewenst - paddle_x
        paddle_x += max(-BREAKOUT_PADDLE_SNELHEID, min(BREAKOUT_PADDLE_SNELHEID, verschil))
        paddle_x = max(0.0, min(float(WIDTH - paddle_b), paddle_x))

        # Draw frame
        canvas = maak_canvas()
        draw = ImageDraw.Draw(canvas)
        for (r, c) in stenen:
            x0 = marge_links + c * steen_b
            y0 = steen_top + r * steen_rij_h
            draw.rectangle([x0, y0, x0 + steen_b - 2, y0 + 2], fill=BREAKOUT_STEEN_KLEUREN[r % len(BREAKOUT_STEEN_KLEUREN)])
        for (r, c), resterend in list(flitsen.items()):
            x0 = marge_links + c * steen_b
            y0 = steen_top + r * steen_rij_h
            draw.rectangle([x0, y0, x0 + steen_b - 2, y0 + 2], fill=(255, 255, 255) if resterend % 2 else (255, 200, 120))
            if resterend <= 1:
                del flitsen[(r, c)]
            else:
                flitsen[(r, c)] = resterend - 1
        draw.rectangle([int(paddle_x), paddle_y, int(paddle_x) + paddle_b - 1, paddle_y + 1], fill=BREAKOUT_PADDLE_KLEUR)
        draw.rectangle([int(bal["x"]), int(bal["y"]), int(bal["x"]) + 1, int(bal["y"]) + 1], fill=BREAKOUT_BAL_KLEUR)

        stuur_via_ddp(canvas)
        time.sleep(BREAKOUT_INTERVAL)

    leeg_canvas = maak_canvas()
    stuur_via_ddp(leeg_canvas)
    animatie_klaar(mijn_id)
    print(f"Breakout finished: {weggespeeld} of {totaal_stenen} bricks destroyed.")

def vuurbal_finale(mijn_id, cx, cy, stappen=13, interval=0.06):
    """Big fireball explosion starting at (cx, cy) that fills the whole screen and fades out.
    Returns False if the animation was cancelled halfway."""
    rng = random.Random()
    hoeken = [(0, 0), (WIDTH, 0), (0, HEIGHT), (WIDTH, HEIGHT)]
    max_afstand = max(math.hypot(cx - hx, cy - hy) for hx, hy in hoeken)
    for stap in range(stappen):
        if not animatie_nog_actief(mijn_id):
            return False
        canvas = maak_canvas()
        if stap == 0:
            teken_vuurbal(canvas, cx, cy, 0, rng, flits=True)
        else:
            voortgang = stap / (stappen - 1)
            radius = max_afstand * min(1.0, voortgang * 1.25)
            verdunning = 1.0
            if stap >= stappen - 3:
                verdunning = 1.0 + (stap - (stappen - 3)) * 0.6
            teken_vuurbal(canvas, cx, cy, radius, rng, verdunning=verdunning)
        stuur_via_ddp(canvas)
        time.sleep(interval)
    return True

def start_pong():
    """Animation 6: Pong. Two automatically played paddles; the ball speeds up on every hit.
    The first to reach PONG_PUNTEN_NODIG points wins, then the animation stops."""
    mijn_id = nieuwe_animatie_id()

    ph = max(3, PONG_PADDLE_HOOGTE)
    links_x = 1
    rechts_x = WIDTH - 3
    centrum = [HEIGHT / 2.0, HEIGHT / 2.0]
    offset = [0.0, 0.0]
    kijkt_naar = [False, False]
    score = [0, 0]
    bal = {}
    pauze = 0

    def serve(richting):
        s = PONG_BAL_SNELHEID
        vy = random.uniform(-0.5, 0.5) * s
        vx = richting * math.sqrt(max(0.05, s * s - vy * vy))
        bal.update({"x": WIDTH / 2.0 - 1, "y": random.uniform(8, HEIGHT - 10), "vx": vx, "vy": vy})
        kijkt_naar[0] = kijkt_naar[1] = False

    def kaats(i):
        richting = 1 if i == 0 else -1
        rel = ((bal["y"] + 1) - centrum[i]) / (ph / 2.0)
        rel = max(-1.0, min(1.0, rel))
        snelheid = min(PONG_BAL_MAX_SNELHEID, math.hypot(bal["vx"], bal["vy"]) + PONG_BAL_VERSNELLING)
        vy = rel * snelheid * 0.7
        bal["vx"] = richting * math.sqrt(max(0.05, snelheid ** 2 - vy ** 2))
        bal["vy"] = vy
        bal["x"] = links_x + 2.0 if i == 0 else rechts_x - 2.0

    def teken(flits=None):
        canvas = maak_canvas()
        draw = ImageDraw.Draw(canvas)
        if PONG_TOON_SCORE:
            f = max(1, PONG_SCORE_SCHAAL)
            tekst_l, tekst_r = str(score[0]), str(score[1])
            kleur_l = kleur_r = PONG_SCORE_KLEUR
            if flits is not None:
                if flits[0] == 0:
                    kleur_l = (255, 255, 255) if flits[1] else PONG_PADDLE_KLEUR_LINKS
                else:
                    kleur_r = (255, 255, 255) if flits[1] else PONG_PADDLE_KLEUR_RECHTS
            teken_temperatuur_compact(canvas, tekst_l, WIDTH // 2 - 5 - len(tekst_l) * (3 * f + 1), 2, kleur_l, f, 1)
            teken_temperatuur_compact(canvas, tekst_r, WIDTH // 2 + 5, 2, kleur_r, f, 1)
        if PONG_TOON_MIDDENLIJN:
            for y in range(0, HEIGHT, 4):
                draw.line([(WIDTH // 2, y), (WIDTH // 2, y + 1)], fill=(60, 60, 60))
        for i, (px, kleur) in enumerate([(links_x, PONG_PADDLE_KLEUR_LINKS), (rechts_x, PONG_PADDLE_KLEUR_RECHTS)]):
            if flits is not None and flits[0] == i and flits[1]:
                kleur = (255, 255, 255)
            top = int(centrum[i] - ph / 2.0)
            draw.rectangle([px, top, px + 1, top + ph - 1], fill=kleur)
        draw.rectangle([int(bal["x"]), int(bal["y"]), int(bal["x"]) + 1, int(bal["y"]) + 1], fill=PONG_BAL_KLEUR)
        return canvas

    serve(random.choice([-1, 1]))
    start = time.time()
    winnaar = None

    while animatie_nog_actief(mijn_id) and winnaar is None:
        if time.time() - start > PONG_MAX_DUUR:
            break

        if pauze > 0:
            pauze -= 1
        else:
            snelheid = math.hypot(bal["vx"], bal["vy"])
            n = max(1, int(math.ceil(snelheid / 0.5)))
            for _ in range(n):
                bal["x"] += bal["vx"] / n
                bal["y"] += bal["vy"] / n

                if bal["y"] < 0:
                    bal["y"] = 0.0
                    bal["vy"] = abs(bal["vy"])
                elif bal["y"] > HEIGHT - 2:
                    bal["y"] = float(HEIGHT - 2)
                    bal["vy"] = -abs(bal["vy"])

                # Paddle collisions
                if bal["vx"] < 0 and links_x - 1.5 <= bal["x"] <= links_x + 2:
                    top = centrum[0] - ph / 2.0
                    if bal["y"] + 2 > top and bal["y"] < top + ph:
                        kaats(0)
                elif bal["vx"] > 0 and rechts_x <= bal["x"] + 2 <= rechts_x + 3.5:
                    top = centrum[1] - ph / 2.0
                    if bal["y"] + 2 > top and bal["y"] < top + ph:
                        kaats(1)

                # Point scored?
                if bal["x"] < -2:
                    score[1] += 1
                    pauze = 12
                    serve(-1)  # serve towards the player who lost the point
                    break
                elif bal["x"] > WIDTH:
                    score[0] += 1
                    pauze = 12
                    serve(1)
                    break

            if max(score) >= PONG_PUNTEN_NODIG:
                winnaar = 0 if score[0] > score[1] else 1

        # Paddles follow the ball; now and then they deliberately aim off
        for i in (0, 1):
            richting_naar = (bal["vx"] < 0) if i == 0 else (bal["vx"] > 0)
            if richting_naar and not kijkt_naar[i]:
                if random.random() < PONG_FOUT_KANS:
                    offset[i] = random.choice([-1, 1]) * (ph / 2.0 + random.uniform(2.0, 4.0))
                else:
                    offset[i] = random.uniform(-ph / 2.0 + 1.5, ph / 2.0 - 1.5)
            kijkt_naar[i] = richting_naar
            doel = (bal["y"] + 1 + offset[i]) if richting_naar else HEIGHT / 2.0
            max_stap = PONG_PADDLE_SNELHEID if richting_naar else PONG_PADDLE_SNELHEID * 0.4
            centrum[i] += max(-max_stap, min(max_stap, doel - centrum[i]))
            centrum[i] = max(ph / 2.0, min(HEIGHT - ph / 2.0, centrum[i]))

        stuur_via_ddp(teken())
        time.sleep(PONG_INTERVAL)

    if winnaar is not None and PONG_EINDE_FLITS:
        for k in range(10):
            if not animatie_nog_actief(mijn_id):
                return
            stuur_via_ddp(teken(flits=(winnaar, k % 2 == 0)))
            time.sleep(0.12)

    stuur_via_ddp(maak_canvas())
    animatie_klaar(mijn_id)
    print(f"Pong finished: {score[0]} - {score[1]}")

def start_snake():
    """Animation 7: Snake. The snake finds the shortest path to the apple (BFS) and avoids itself.
    After SNAKE_APPELS_NODIG apples the animation stops (optionally with an explosion)."""
    from collections import deque

    mijn_id = nieuwe_animatie_id()

    cel = max(1, SNAKE_CEL)
    kol, rij = WIDTH // cel, HEIGHT // cel
    muren = SNAKE_MUREN
    richtingen = [(1, 0), (-1, 0), (0, 1), (0, -1)]

    def is_muur(c, r):
        return muren and (c == 0 or r == 0 or c == kol - 1 or r == rij - 1)

    def vrij(c, r, bezet):
        return 0 <= c < kol and 0 <= r < rij and not is_muur(c, r) and (c, r) not in bezet

    def stap(c, r, d):
        nc, nr = c + d[0], r + d[1]
        if not muren:
            nc %= kol
            nr %= rij
        return nc, nr

    def eerste_stap_naar(start, doel, bezet):
        wachtrij = deque([start])
        vorige = {start: None}
        while wachtrij:
            huidig = wachtrij.popleft()
            if huidig == doel:
                break
            for d in richtingen:
                n = stap(huidig[0], huidig[1], d)
                if n not in vorige and vrij(n[0], n[1], bezet):
                    vorige[n] = huidig
                    wachtrij.append(n)
        if doel not in vorige:
            return None
        p = doel
        while vorige[p] != start:
            p = vorige[p]
        return p

    def ruimte(start, bezet):
        gezien = {start}
        wachtrij = deque([start])
        while wachtrij:
            huidig = wachtrij.popleft()
            for d in richtingen:
                n = stap(huidig[0], huidig[1], d)
                if n not in gezien and vrij(n[0], n[1], bezet):
                    gezien.add(n)
                    wachtrij.append(n)
        return len(gezien)

    lengte = max(2, min(SNAKE_START_LENGTE, kol // 2 - 2))
    kop0 = (kol // 2, rij // 2)
    body = [(kop0[0] - i, kop0[1]) for i in range(lengte)]
    groei = 0
    appels = 0

    def nieuwe_appel():
        bezet = set(body)
        vrije = [(c, r) for c in range(kol) for r in range(rij) if vrij(c, r, bezet)]
        return random.choice(vrije) if vrije else None

    appel = nieuwe_appel()

    def lerp(k1, k2, t):
        return tuple(int(k1[i] + (k2[i] - k1[i]) * t) for i in range(3))

    def teken(flits=False):
        canvas = maak_canvas()
        draw = ImageDraw.Draw(canvas)
        if muren:
            draw.rectangle([0, 0, kol * cel - 1, rij * cel - 1], outline=SNAKE_MUUR_KLEUR, width=cel)
        if appel is not None:
            draw.rectangle([appel[0] * cel, appel[1] * cel, appel[0] * cel + cel - 1, appel[1] * cel + cel - 1],
                           fill=SNAKE_APPEL_KLEUR)
        for idx, (c, r) in enumerate(body):
            if flits:
                kleur = (255, 255, 255)
            elif idx == 0:
                kleur = SNAKE_KOP_KLEUR
            else:
                kleur = lerp(SNAKE_LICHAAM_KLEUR, SNAKE_STAART_KLEUR, idx / max(1, len(body) - 1))
            draw.rectangle([c * cel, r * cel, c * cel + cel - 1, r * cel + cel - 1], fill=kleur)
        return canvas

    start = time.time()
    gecrasht = False

    while animatie_nog_actief(mijn_id) and appels < SNAKE_APPELS_NODIG:
        if time.time() - start > SNAKE_MAX_DUUR:
            break

        kop = body[0]
        bezet = set(body) if groei > 0 else set(body[:-1])

        volgende = None
        if appel is not None:
            volgende = eerste_stap_naar(kop, appel, bezet)
            # Avoid ending up in a space that is too small / closed off
            if volgende is not None and ruimte(volgende, bezet | {kop}) < len(body):
                volgende = None
        if volgende is None:
            beste, beste_ruimte = None, -1
            for d in richtingen:
                n = stap(kop[0], kop[1], d)
                if vrij(n[0], n[1], bezet):
                    rm = ruimte(n, bezet | {kop})
                    if rm > beste_ruimte:
                        beste, beste_ruimte = n, rm
            volgende = beste
        if volgende is None:
            gecrasht = True
            break

        body.insert(0, volgende)
        if appel is not None and volgende == appel:
            appels += 1
            groei += SNAKE_GROEI_PER_APPEL
            appel = nieuwe_appel()
        if groei > 0:
            groei -= 1
        else:
            body.pop()

        stuur_via_ddp(teken())
        time.sleep(SNAKE_STAP_INTERVAL)

    if animatie_nog_actief(mijn_id) and SNAKE_EINDE_EXPLOSIE:
        for k in range(6):
            stuur_via_ddp(teken(flits=(k % 2 == 0)))
            time.sleep(0.1)
        kop_px = body[0][0] * cel + cel / 2.0
        kop_py = body[0][1] * cel + cel / 2.0
        if not vuurbal_finale(mijn_id, kop_px, kop_py):
            return

    stuur_via_ddp(maak_canvas())
    animatie_klaar(mijn_id)
    print(f"Snake finished: {appels} apples eaten{' (crashed)' if gecrasht else ''}.")

def tetris_rotaties(cellen):
    """All unique rotations of a piece, normalised so the top-left cell is at (0, 0)."""
    uit = []
    huidig = list(cellen)
    for _ in range(4):
        minx = min(x for x, y in huidig)
        miny = min(y for x, y in huidig)
        norm = tuple(sorted((x - minx, y - miny) for x, y in huidig))
        if norm not in uit:
            uit.append(norm)
        huidig = [(-y, x) for x, y in huidig]
    return uit

def start_tetris():
    """Animation 8: Tetris. An automatic player picks the best position and rotation for each piece
    using a board-evaluation heuristic. After TETRIS_RIJEN_NODIG cleared rows the animation stops."""
    mijn_id = nieuwe_animatie_id()

    cel = max(1, TETRIS_CEL)
    b = max(4, min(TETRIS_BREEDTE, WIDTH // cel))
    h = max(6, min(TETRIS_HOOGTE, HEIGHT // cel))
    fw, fh = b * cel, h * cel
    fx, fy = (WIDTH - fw) // 2, (HEIGHT - fh) // 2

    rotaties = {naam: tetris_rotaties(cellen) for naam, (cellen, kleur) in TETRIS_STUKKEN.items()}
    veld = [[None] * b for _ in range(h)]

    def past(vorm, x, y):
        for cx, cy in vorm:
            nx, ny = x + cx, y + cy
            if nx < 0 or nx >= b or ny >= h:
                return False
            if ny >= 0 and veld[ny][nx] is not None:
                return False
        return True

    def beoordeel(naam):
        kandidaten = []
        for ri, vorm in enumerate(rotaties[naam]):
            breedte = max(x for x, y in vorm) + 1
            for x in range(0, b - breedte + 1):
                if not past(vorm, x, 0):
                    continue
                y = 0
                while past(vorm, x, y + 1):
                    y += 1
                kopie = [r[:] for r in veld]
                for cx, cy in vorm:
                    kopie[y + cy][x + cx] = 1
                rest = [r for r in kopie if not all(v is not None for v in r)]
                volle = h - len(rest)
                rest = [[None] * b for _ in range(volle)] + rest
                hoogtes, gaten = [], 0
                for c in range(b):
                    hoogte, gevonden = 0, False
                    for r in range(h):
                        if rest[r][c] is not None:
                            if not gevonden:
                                hoogte, gevonden = h - r, True
                        elif gevonden:
                            gaten += 1
                    hoogtes.append(hoogte)
                bult = sum(abs(hoogtes[i] - hoogtes[i + 1]) for i in range(b - 1))
                score = -0.51 * sum(hoogtes) + 0.76 * volle - 0.36 * gaten - 0.18 * bult
                kandidaten.append((score, ri, x))
        kandidaten.sort(reverse=True)
        return kandidaten

    lijnen = 0
    volgende = None

    def teken(actief=None, flash_rijen=(), flash_kleur=(255, 255, 255)):
        canvas = maak_canvas()
        draw = ImageDraw.Draw(canvas)
        draw.rectangle([fx - 1, fy - 1, fx + fw, fy + fh], outline=TETRIS_KADER_KLEUR)
        for r in range(h):
            for c in range(b):
                k = flash_kleur if r in flash_rijen else veld[r][c]
                if k:
                    draw.rectangle([fx + c * cel, fy + r * cel, fx + c * cel + cel - 1, fy + r * cel + cel - 1], fill=k)
        if actief:
            vorm, x, y, k = actief
            for cx, cy in vorm:
                if y + cy >= 0:
                    px, py = fx + (x + cx) * cel, fy + (y + cy) * cel
                    draw.rectangle([px, py, px + cel - 1, py + cel - 1], fill=k)
        if TETRIS_TOON_RIJEN:
            tekst = str(lijnen)
            breedte = len(tekst) * 7 - 1
            teken_temperatuur_compact(canvas, tekst, max(0, fx - 4 - breedte), fy + 2, TETRIS_INFO_KLEUR, 2, 1)
        if TETRIS_TOON_VOLGENDE and volgende:
            vorm = rotaties[volgende][0]
            kleur = TETRIS_STUKKEN[volgende][1]
            for cx, cy in vorm:
                px, py = fx + fw + 5 + cx * cel, fy + 2 + cy * cel
                draw.rectangle([px, py, px + cel - 1, py + cel - 1], fill=kleur)
        return canvas

    def flits(rijen, kleur, keer=6):
        for k in range(keer):
            stuur_via_ddp(teken(flash_rijen=set(rijen) if k % 2 == 0 else (), flash_kleur=kleur))
            time.sleep(0.06)

    namen = list(TETRIS_STUKKEN.keys())
    volgende = random.choice(namen)
    start = time.time()

    while animatie_nog_actief(mijn_id) and lijnen < TETRIS_RIJEN_NODIG:
        if time.time() - start > TETRIS_MAX_DUUR:
            break

        naam = volgende
        volgende = random.choice(namen)
        vormen = rotaties[naam]
        kleur = TETRIS_STUKKEN[naam][1]
        kandidaten = beoordeel(naam)

        ri = 0
        x = (b - (max(cx for cx, cy in vormen[0]) + 1)) // 2
        y = 0
        if not kandidaten or not past(vormen[0], x, 0):
            # Playfield is full: flash red briefly and start over
            flits(range(h), (255, 40, 40))
            for r in range(h):
                veld[r] = [None] * b
            continue

        if len(kandidaten) > 1 and random.random() < TETRIS_FOUT_KANS:
            _, doel_ri, doel_x = random.choice(kandidaten[1:min(4, len(kandidaten))])
        else:
            _, doel_ri, doel_x = kandidaten[0]

        while animatie_nog_actief(mijn_id):
            geland = False
            uitgelijnd = True

            if ri != doel_ri:
                uitgelijnd = False
                nieuw_ri = (ri + 1) % len(vormen)
                gelukt = False
                for dx in (0, -1, 1, -2, 2):
                    if past(vormen[nieuw_ri], x + dx, y):
                        ri, x, gelukt = nieuw_ri, x + dx, True
                        break
                if not gelukt:
                    doel_ri = ri

            if x != doel_x:
                uitgelijnd = False
                richting = 1 if doel_x > x else -1
                if past(vormen[ri], x + richting, y):
                    x += richting
                else:
                    doel_x = x

            if uitgelijnd:
                for _ in range(3 if TETRIS_SNELLE_VAL else 1):
                    if past(vormen[ri], x, y + 1):
                        y += 1
                    else:
                        geland = True
                        break

            stuur_via_ddp(teken(actief=(vormen[ri], x, y, kleur)))
            time.sleep(TETRIS_VAL_INTERVAL)
            if geland:
                break

        if not animatie_nog_actief(mijn_id):
            return

        for cx, cy in vormen[ri]:
            if 0 <= y + cy < h:
                veld[y + cy][x + cx] = kleur

        volle = [r for r in range(h) if all(v is not None for v in veld[r])]
        if volle:
            flits(volle, (255, 255, 255))
            overig = [veld[r] for r in range(h) if r not in volle]
            veld[:] = [[None] * b for _ in volle] + overig
            lijnen += len(volle)

    if animatie_nog_actief(mijn_id) and lijnen >= TETRIS_RIJEN_NODIG and TETRIS_EINDE_EXPLOSIE:
        flits(range(h), (255, 255, 255), keer=6)
        if not vuurbal_finale(mijn_id, fx + fw / 2.0, fy + fh / 2.0):
            return

    stuur_via_ddp(maak_canvas())
    animatie_klaar(mijn_id)
    print(f"Tetris finished: {lijnen} rows cleared.")

def start_missile_command():
    """Animation 9: Missile Command. Enemy missiles fall onto the cities; the battery in the
    middle shoots them down automatically (with predictive aiming). After
    MISSILE_AFGESCHOTEN_NODIG kills the animation stops."""
    mijn_id = nieuwe_animatie_id()

    grond_y = HEIGHT - 2
    stad_top = grond_y - 4
    bat_x = WIDTH // 2
    bat_y = grond_y - 3
    n = max(1, MISSILE_STEDEN)
    links = n // 2
    rechts = n - links
    centra = []
    for i in range(links):
        centra.append(4 + (22 * i / max(1, links - 1) if links > 1 else 11))
    for i in range(rechts):
        centra.append(40 + (20 * i / max(1, rechts - 1) if rechts > 1 else 10))
    steden = [{"x": int(round(c)), "leeft": True} for c in centra]

    vijanden, verdediging, explosies = [], [], []
    afgeschoten = 0
    volgende_spawn = 0
    laatste_schot = -999
    frame = 0
    spoor_fps = max(1, int(round(MISSILE_VUUR_INTERVAL / MISSILE_INTERVAL)))

    def nieuwe_vijand():
        levend = [c for c in steden if c["leeft"]]
        if levend and random.random() < 0.85:
            doel_x = random.choice(levend)["x"] + random.uniform(-1, 1)
        else:
            doel_x = random.uniform(2, WIDTH - 3)
        x0 = random.uniform(2, WIDTH - 3)
        dx, dy = doel_x - x0, stad_top - 0.0
        d = math.hypot(dx, dy)
        v = MISSILE_VIJAND_SNELHEID * random.uniform(0.85, 1.2)
        vijanden.append({"x0": x0, "y0": 0.0, "x": x0, "y": 0.0, "vx": dx / d * v, "vy": dy / d * v,
                         "doel_x": doel_x, "negeer": random.random() < MISSILE_DOORLAATKANS,
                         "toegewezen": False})

    def teken():
        canvas = maak_canvas()
        draw = ImageDraw.Draw(canvas)
        draw.rectangle([0, grond_y, WIDTH - 1, HEIGHT - 1], fill=MISSILE_GROND_KLEUR)
        for c in steden:
            x = c["x"] - 2
            if c["leeft"]:
                draw.rectangle([x, stad_top + 1, x + 4, grond_y - 1], fill=MISSILE_STAD_KLEUR)
                draw.point((x, stad_top), fill=MISSILE_STAD_KLEUR)
                draw.point((x + 2, stad_top), fill=MISSILE_STAD_KLEUR)
                draw.point((x + 4, stad_top), fill=MISSILE_STAD_KLEUR)
                draw.point((x + 1, stad_top + 2), fill=(255, 230, 120))
                draw.point((x + 3, stad_top + 2), fill=(255, 230, 120))
            else:
                draw.point((x + 1, grond_y - 1), fill=(90, 60, 40))
                draw.point((x + 3, grond_y - 1), fill=(90, 60, 40))
                draw.point((x + 2, grond_y - 2), fill=(60, 40, 30))
        # battery
        draw.polygon([(bat_x - 3, grond_y - 1), (bat_x + 3, grond_y - 1), (bat_x, bat_y)], fill=(230, 230, 230))
        for v in vijanden:
            draw.line([(int(v["x0"]), int(v["y0"])), (int(v["x"]), int(v["y"]))], fill=MISSILE_VIJAND_SPOOR_KLEUR)
            zet_pixel(canvas, int(v["x"]), int(v["y"]), MISSILE_VIJAND_KOP_KLEUR)
        for m in verdediging:
            draw.line([(bat_x, bat_y), (int(m["x"]), int(m["y"]))], fill=MISSILE_VERDEDIGING_SPOOR_KLEUR)
            zet_pixel(canvas, int(m["x"]), int(m["y"]), (255, 255, 255))
        for e in explosies:
            r = e["straal"]
            if r < 0.5:
                zet_pixel(canvas, int(e["x"]), int(e["y"]), (255, 255, 255))
                continue
            kleuren = [(255, 90, 0), (255, 190, 30), (255, 245, 200)]
            f = e["leeftijd"] / float(e["duur"])
            buiten = kleuren[0] if f > 0.6 else kleuren[1]
            draw.ellipse([e["x"] - r, e["y"] - r, e["x"] + r, e["y"] + r], fill=buiten)
            if r > 2:
                draw.ellipse([e["x"] - r * 0.5, e["y"] - r * 0.5, e["x"] + r * 0.5, e["y"] + r * 0.5], fill=kleuren[2])
        if MISSILE_TOON_SCORE:
            teken_temperatuur_compact(canvas, str(afgeschoten), 1, 1, MISSILE_SCORE_KLEUR, 1, 1)
        return canvas

    def voeg_explosie_toe(x, y, schaal, dodelijk):
        explosies.append({"x": x, "y": y, "leeftijd": 0, "duur": max(4, MISSILE_EXPLOSIE_DUUR),
                          "max": MISSILE_EXPLOSIE_RADIUS * schaal, "straal": 0.0, "dodelijk": dodelijk})

    def probeer_te_schieten():
        nonlocal laatste_schot
        if frame - laatste_schot < spoor_fps or len(verdediging) >= MISSILE_MAX_VERDEDIGING:
            return
        ds = MISSILE_VERDEDIGING_SNELHEID
        beste = None
        for v in vijanden:
            if v["toegewezen"] or v["negeer"]:
                continue
            for t in range(1, 400):
                ex, ey = v["x"] + v["vx"] * t, v["y"] + v["vy"] * t
                if ey > stad_top - 3:
                    break
                afstand = math.hypot(ex - bat_x, ey - bat_y)
                if afstand / ds <= t and ey > 3:
                    if beste is None or ey > beste[1]:
                        beste = (v, ey, ex, ey)
                    break
        if beste:
            v, _, ex, ey = beste
            v["toegewezen"] = True
            verdediging.append({"x": float(bat_x), "y": float(bat_y), "doel_x": ex, "doel_y": ey})
            laatste_schot = frame

    start = time.time()
    while animatie_nog_actief(mijn_id) and afgeschoten < MISSILE_AFGESCHOTEN_NODIG:
        if time.time() - start > MISSILE_MAX_DUUR:
            break
        frame += 1

        if frame >= volgende_spawn and len(vijanden) < MISSILE_MAX_TEGELIJK:
            nieuwe_vijand()
            volgende_spawn = frame + int(random.uniform(MISSILE_SPAWN_MIN, MISSILE_SPAWN_MAX) / MISSILE_INTERVAL)

        # enemies move / impact
        for v in vijanden[:]:
            v["x"] += v["vx"]
            v["y"] += v["vy"]
            if v["y"] >= stad_top:
                vijanden.remove(v)
                voeg_explosie_toe(v["x"], min(v["y"], grond_y - 1), 0.7, False)
                for c in steden:
                    if c["leeft"] and abs(c["x"] - v["x"]) <= 3:
                        c["leeft"] = False

        probeer_te_schieten()

        # defence missiles move
        for m in verdediging[:]:
            dx, dy = m["doel_x"] - m["x"], m["doel_y"] - m["y"]
            d = math.hypot(dx, dy)
            if d <= MISSILE_VERDEDIGING_SNELHEID:
                verdediging.remove(m)
                voeg_explosie_toe(m["doel_x"], m["doel_y"], 1.0, True)
            else:
                m["x"] += dx / d * MISSILE_VERDEDIGING_SNELHEID
                m["y"] += dy / d * MISSILE_VERDEDIGING_SNELHEID

        # explosions grow and shrink; lethal explosions hit enemies
        for e in explosies[:]:
            e["leeftijd"] += 1
            f = e["leeftijd"] / float(e["duur"])
            if f >= 1.0:
                explosies.remove(e)
                continue
            e["straal"] = e["max"] * (f * 2 if f < 0.5 else (1 - f) * 2)
            if e["dodelijk"]:
                for v in vijanden[:]:
                    if math.hypot(v["x"] - e["x"], v["y"] - e["y"]) <= e["straal"] + 1:
                        vijanden.remove(v)
                        afgeschoten += 1
                        voeg_explosie_toe(v["x"], v["y"], 0.4, False)

        stuur_via_ddp(teken())
        time.sleep(MISSILE_INTERVAL)

    if animatie_nog_actief(mijn_id) and afgeschoten >= MISSILE_AFGESCHOTEN_NODIG and MISSILE_EINDE_EXPLOSIE:
        vijanden.clear()
        verdediging.clear()
        explosies.clear()
        if vuurbal_finale(mijn_id, bat_x, bat_y):
            pass
        else:
            return

    stuur_via_ddp(maak_canvas())
    animatie_klaar(mijn_id)
    print(f"Missile Command finished: {afgeschoten} shot down, {sum(1 for c in steden if c['leeft'])} cities left")

def start_scroll_tekst(tekst):
    """Scroll a text once across the matrix from right to left, then stop streaming."""
    mijn_id = nieuwe_animatie_id()
    tekst_breedte = len(tekst) * 6
    y_pos = (HEIGHT - 7) // 2  
    x_pos = WIDTH
    
    while animatie_nog_actief(mijn_id) and x_pos > -tekst_breedte:
        canvas = maak_canvas()
        teken_tekst_flexibel(canvas, tekst, int(x_pos), y_pos, TEKST_KLEUR, factor=1, spatiëring=1)
        stuur_via_ddp(canvas)
        
        x_pos -= 1
        time.sleep(SCROLL_INTERVAL)

    animatie_klaar(mijn_id)

def teken_grote_vuurbal(canvas, center_x, center_y, stap):
    """Draw a simple random-pixel fireball (used by the Galaxian destruction animation)."""
    kleuren = [(255, 255, 255), (255, 255, 0), (255, 140, 0), (255, 60, 0), (200, 0, 0)]
    radius = 3 + stap * 2
    for _ in range(35 + stap * 8):
        fx = center_x + random.randint(-radius, radius)
        fy = center_y + random.randint(-radius, radius)
        fkleur = random.choice(kleuren)
        zet_pixel(canvas, fx, fy, fkleur)

def start_destructie_animatie(weer_code, temperatuur_celsius):
    """Animation 3: a Galaxian ship shoots the weather screen apart piece by piece
    (temperature, thermometer, weather icon), flies a lap and leaves with a smoke trail."""
    mijn_id = nieuwe_animatie_id()

    if GALAXIAN_SPRITE is None:
        print("Galaxian sprite missing!")
        animatie_klaar(mijn_id)
        return

    # 1. Build the initial weather screen
    canvas = maak_canvas()
    weer_icoon = WEER_ICONEN_CACHE.get(weer_code)
    plak_icoon(canvas, weer_icoon, WEERICOON_X, WEERICOON_Y)
    plak_icoon(canvas, THERMOMETER_ICOON_CACHE, THERMO_X, THERMO_Y)
    if temperatuur_celsius is not None:
        teken_temperatuur(canvas, temperatuur_celsius, TEMP_TEKST_X, TEMP_TEKST_Y, TEMP_TEKST_KLEUR)

    def teken_raket_met_sparkles(c, rx, ry, sprite_to_draw):
        s_w, s_h = sprite_to_draw.width, sprite_to_draw.height
        for sy in range(ry + s_h, HEIGHT):
            if random.random() > 0.3:
                sparkle_kleur = random.choice([(150, 150, 150), (220, 220, 220), (255, 255, 255), (255, 140, 0)])
                zet_pixel(c, rx + s_w//2 + random.randint(-2, 2), sy, sparkle_kleur)
        plak_icoon(c, sprite_to_draw, rx, ry)

    # -------------------------------------------------------------
    # STEP 1: Target 1: temperature
    # -------------------------------------------------------------
    schip_y = HEIGHT
    schip_x = 50
    doel_temp_x, doel_temp_y = TEMP_TEKST_X + 10, TEMP_TEKST_Y + 5

    for y in range(HEIGHT, 22, -1):
        if not animatie_nog_actief(mijn_id): return
        fc = canvas.copy()
        sprite_links = GALAXIAN_SPRITE.rotate(45, resample=Image.NEAREST, expand=True)
        teken_raket_met_sparkles(fc, schip_x, y, sprite_links)
        stuur_via_ddp(fc)
        time.sleep(0.05)

    schip_y = 22

    for shot in range(2):
        start_px, start_py = schip_x, schip_y
        end_px, end_py = doel_temp_x, doel_temp_y
        steps = 6
        for step in range(steps + 1):
            if not animatie_nog_actief(mijn_id): return
            fc = canvas.copy()
            sprite_links = GALAXIAN_SPRITE.rotate(45, resample=Image.NEAREST, expand=True)
            teken_raket_met_sparkles(fc, schip_x, schip_y, sprite_links)
            
            curr_x = int(start_px + (end_px - start_px) * (step / steps))
            curr_y = int(start_py + (end_py - start_py) * (step / steps))
            zet_pixel(fc, curr_x, curr_y, (255, 0, 0))
            stuur_via_ddp(fc)
            time.sleep(0.03)

    for s in range(8):
        if not animatie_nog_actief(mijn_id): return
        fc = canvas.copy()
        sprite_links = GALAXIAN_SPRITE.rotate(45, resample=Image.NEAREST, expand=True)
        teken_raket_met_sparkles(fc, schip_x, schip_y, sprite_links)
        teken_grote_vuurbal(fc, doel_temp_x, doel_temp_y, s)
        stuur_via_ddp(fc)
        time.sleep(0.07)

    draw = ImageDraw.Draw(canvas)
    draw.rectangle([TEMP_TEKST_X - 2, TEMP_TEKST_Y - 2, WIDTH, HEIGHT], fill=(0, 0, 0))
    stuur_via_ddp(canvas)
    time.sleep(0.2)

    # -------------------------------------------------------------
    # STEP 2: Target 2: thermometer
    # -------------------------------------------------------------
    doel_thermo_x, doel_thermo_y = THERMO_X + 7, THERMO_Y + 7
    target_x, target_y = 25, 12

    for y in range(schip_y, target_y, -1):
        if not animatie_nog_actief(mijn_id): return
        fc = canvas.copy()
        sprite_omhoog = GALAXIAN_SPRITE.rotate(0, resample=Image.NEAREST, expand=True)
        teken_raket_met_sparkles(fc, schip_x, y, sprite_omhoog)
        stuur_via_ddp(fc)
        time.sleep(0.05)

    schip_y = target_y

    for x in range(schip_x, target_x, -1):
        if not animatie_nog_actief(mijn_id): return
        fc = canvas.copy()
        sprite_rechts = GALAXIAN_SPRITE.rotate(-45, resample=Image.NEAREST, expand=True)
        teken_raket_met_sparkles(fc, x, schip_y, sprite_rechts)
        stuur_via_ddp(fc)
        time.sleep(0.05)

    schip_x = target_x

    for shot in range(2):
        start_px, start_py = schip_x + 5, schip_y
        end_px, end_py = doel_thermo_x, doel_thermo_y
        steps = 5
        for step in range(steps + 1):
            if not animatie_nog_actief(mijn_id): return
            fc = canvas.copy()
            sprite_rechts = GALAXIAN_SPRITE.rotate(-45, resample=Image.NEAREST, expand=True)
            teken_raket_met_sparkles(fc, schip_x, schip_y, sprite_rechts)
            
            curr_x = int(start_px + (end_px - start_px) * (step / steps))
            curr_y = int(start_py + (end_py - start_py) * (step / steps))
            zet_pixel(fc, curr_x, curr_y, (255, 0, 0))
            stuur_via_ddp(fc)
            time.sleep(0.03)

    for s in range(8):
        if not animatie_nog_actief(mijn_id): return
        fc = canvas.copy()
        sprite_rechts = GALAXIAN_SPRITE.rotate(-45, resample=Image.NEAREST, expand=True)
        teken_raket_met_sparkles(fc, schip_x, schip_y, sprite_rechts)
        teken_grote_vuurbal(fc, doel_thermo_x, doel_thermo_y, s)
        stuur_via_ddp(fc)
        time.sleep(0.07)

    draw.rectangle([THERMO_X - 1, THERMO_Y - 1, THERMO_X + THERMO_GROOTTE + 1, THERMO_Y + THERMO_GROOTTE + 1], fill=(0, 0, 0))
    stuur_via_ddp(canvas)
    time.sleep(0.2)

    # -------------------------------------------------------------
    # STEP 3: Target 3: weather icon
    # -------------------------------------------------------------
    target_right_x = 52
    target_right_y = 10

    for x in range(schip_x, target_right_x):
        if not animatie_nog_actief(mijn_id): return
        fc = canvas.copy()
        sprite_links_schuin = GALAXIAN_SPRITE.rotate(45, resample=Image.NEAREST, expand=True)
        teken_raket_met_sparkles(fc, x, schip_y, sprite_links_schuin)
        stuur_via_ddp(fc)
        time.sleep(0.04)

    schip_x = target_right_x
    doel_icoon_x = WEERICOON_X + WEERICOON_GROOTTE // 2
    doel_icoon_y = WEERICOON_Y + WEERICOON_GROOTTE // 2

    for shot in range(8):
        start_px, start_py = schip_x, schip_y
        end_px, end_py = doel_icoon_x, doel_icoon_y
        steps = 4
        for step in range(steps + 1):
            if not animatie_nog_actief(mijn_id): return
            fc = canvas.copy()
            sprite_recht_links = GALAXIAN_SPRITE.rotate(90, resample=Image.NEAREST, expand=True)
            teken_raket_met_sparkles(fc, schip_x, schip_y, sprite_recht_links)
            
            curr_x = int(start_px + (end_px - start_px) * (step / steps))
            curr_y = int(start_py + (end_py - start_py) * (step / steps))
            zet_pixel(fc, curr_x, curr_y, (255, 255, 0))
            stuur_via_ddp(fc)
            time.sleep(0.02)

    for s in range(12):
        if not animatie_nog_actief(mijn_id): return
        fc = canvas.copy()
        sprite_recht_links = GALAXIAN_SPRITE.rotate(90, resample=Image.NEAREST, expand=True)
        teken_raket_met_sparkles(fc, schip_x, schip_y, sprite_recht_links)
        teken_grote_vuurbal(fc, doel_icoon_x, doel_icoon_y, s)
        stuur_via_ddp(fc)
        time.sleep(0.07)

    canvas = maak_canvas()
    stuur_via_ddp(canvas)
    time.sleep(0.3)

    # -------------------------------------------------------------
    # STEP 4: A lap around the whole matrix
    # -------------------------------------------------------------
    center_x, center_y = 32, 16
    rx_path, ry_path = 24, 12
    angles = [i * 0.10 for i in range(int(2 * math.pi / 0.10) + 3)]
    
    for angle in angles:
        if not animatie_nog_actief(mijn_id): return
        frame_canvas = maak_canvas()
        
        cur_x = int(center_x + rx_path * math.cos(angle) - GALAXIAN_SPRITE.width // 2)
        cur_y = int(center_y + ry_path * math.sin(angle) - GALAXIAN_SPRITE.height // 2)

        dx = -rx_path * math.sin(angle)
        dy = ry_path * math.cos(angle)
        deg = math.degrees(math.atan2(dy, dx)) - 90
        
        rotated_ship = GALAXIAN_SPRITE.rotate(deg, resample=Image.NEAREST, expand=True)
        r_w, r_h = rotated_ship.width, rotated_ship.height

        for _ in range(3):
            sparkle_kleur = random.choice([(255, 255, 255), (255, 200, 0), (255, 120, 0), (180, 180, 180)])
            zet_pixel(frame_canvas, cur_x + r_w//2 + random.randint(-3, 3), cur_y + r_h//2 + random.randint(-3, 3), sparkle_kleur)

        plak_icoon(frame_canvas, rotated_ship, cur_x, cur_y)
        stuur_via_ddp(frame_canvas)
        time.sleep(0.035)

    last_x = int(center_x + rx_path * math.cos(angles[-1]) - GALAXIAN_SPRITE.width // 2)
    last_y = int(center_y + ry_path * math.sin(angles[-1]) - GALAXIAN_SPRITE.height // 2)

    # -------------------------------------------------------------
    # STEP 5: Finale - fly off to the left with a smoke trail visibly behind the ship
    # -------------------------------------------------------------
    left_facing_ship = GALAXIAN_SPRITE.rotate(90, resample=Image.NEAREST, expand=True)
    l_w, l_h = left_facing_ship.width, left_facing_ship.height

    rook_deeltjes = []

    for fx in range(last_x, -l_w - 55, -1):
        if not animatie_nog_actief(mijn_id): return
        frame_canvas = maak_canvas()
        
        # The exhaust is on the right side of the ship (since it flies to the left)
        uitlaat_x = fx + l_w
        uitlaat_y = last_y + l_h // 2
        
        # Add new smoke particles at the exhaust
        for _ in range(7):
            rook_deeltjes.append({
                'x': float(uitlaat_x + random.randint(0, 1)),
                'y': float(uitlaat_y + random.randint(-2, 2)),
                'vx': random.uniform(0.0, 0.3),  # Stays behind, or drifts only slightly to the right
                'vy': random.uniform(-0.6, 0.6),  # Spreads naturally up/down
                'life': random.randint(20, 40),   # Lives long enough for a full plume
                'kleur': random.choice([
                    (255, 255, 255), (255, 220, 0), (255, 140, 0), 
                    (200, 60, 0), (150, 150, 150), (100, 100, 100), (60, 60, 60)
                ])
            })

        # Update and draw all smoke particles
        nieuwe_deeltjes = []
        for p in rook_deeltjes:
            p['x'] += p['vx']
            p['y'] += p['vy']
            p['life'] -= 1
            
            if p['life'] > 0 and 0 <= int(p['x']) < WIDTH and 0 <= int(p['y']) < HEIGHT:
                zet_pixel(frame_canvas, int(p['x']), int(p['y']), p['kleur'])
                nieuwe_deeltjes.append(p)
        rook_deeltjes = nieuwe_deeltjes

        # Draw the ship on top (so the smoke stays behind the exhaust)
        plak_icoon(frame_canvas, left_facing_ship, fx, last_y)
        stuur_via_ddp(frame_canvas)
        time.sleep(0.025)

    # Send a final empty (black) frame and stop streaming
    leeg_canvas = maak_canvas()
    stuur_via_ddp(leeg_canvas)
    animatie_klaar(mijn_id)
    print("Galaxian destruction animation finished.")

# ============================================================
# Space Invaders animation
# ============================================================

def teken_terrein(canvas):
    """Draw the ground (SPACE_INVADERS_TERREIN) at the bottom of the screen."""
    draw = ImageDraw.Draw(canvas)
    for x in range(WIDTH):
        h = SPACE_INVADERS_TERREIN[x] if x < len(SPACE_INVADERS_TERREIN) else 3
        draw.line([(x, HEIGHT - h), (x, HEIGHT - 1)], fill=SPACE_INVADERS_TERREIN_KLEUR)

def terrein_hoogte_op(x):
    """Return the ground height (in pixels) at column x."""
    xi = max(0, min(len(SPACE_INVADERS_TERREIN) - 1, int(x)))
    return SPACE_INVADERS_TERREIN[xi]

def teken_kleine_explosie(canvas, cx, cy, stap):
    """Draw one frame (step 'stap') of a small explosion, e.g. a hit invader."""
    kleuren = [(255, 255, 255), (255, 220, 0), (255, 140, 0), (200, 40, 0)]
    radius = 1 + stap
    for _ in range(10 + stap * 4):
        fx = cx + random.randint(-radius, radius)
        fy = cy + random.randint(-radius, radius)
        zet_pixel(canvas, fx, fy, random.choice(kleuren))

def teken_scherm_explosie_kleur(t, rng):
    """Colour for the full-screen explosion, depending on t = distance/radius
    (0 = white-hot core, 1 = outer edge)."""
    if t < 0.18:
        return rng.choice([(255, 255, 255), (255, 250, 210), (255, 235, 140)])
    elif t < 0.40:
        return rng.choice([(255, 225, 80), (255, 190, 40), (255, 160, 0)])
    elif t < 0.68:
        return rng.choice([(255, 120, 0), (230, 80, 0), (210, 50, 0)])
    else:
        return rng.choice([(180, 30, 0), (140, 15, 0), (100, 10, 0), (70, 5, 0)])

def teken_vuurbal(canvas, cx, cy, radius, rng, verdunning=1.0, flits=False):
    """Draw a growing fireball with a glowing core, orange/red rings and a
    ragged, cloud-like edge (like a real pixel-art explosion)."""
    if flits:
        for y in range(HEIGHT):
            for x in range(WIDTH):
                if rng.random() < 0.8:
                    zet_pixel(canvas, x, y, rng.choice([(255, 255, 255), (255, 240, 180)]))
        return

    for y in range(HEIGHT):
        dy = y - cy
        for x in range(WIDTH):
            dx = x - cx
            afstand = math.hypot(dx, dy)
            if afstand > radius:
                continue
            t = afstand / radius if radius > 0 else 0
            # ragged edge: the chance of a "hole" increases towards the outside
            gat_kans = min(0.97, (0.10 + t * 0.55) * verdunning)
            if rng.random() < gat_kans:
                continue
            zet_pixel(canvas, x, y, teken_scherm_explosie_kleur(t, rng))

def start_space_invaders():
    """Animation 4: Space Invaders. A ship moves left/right (and a little up/down) shooting random
    invaders. After SPACE_INVADERS_HITS_NODIG hits the whole screen explodes and the animation stops."""
    mijn_id = nieuwe_animatie_id()

    if not INVADERS_SPRITES or INVADERS_SCHIP_SPRITE is None:
        print("Cannot start Space Invaders: sprites missing.")
        animatie_klaar(mijn_id)
        return

    schip_breedte = INVADERS_SCHIP_SPRITE.width
    schip_hoogte = INVADERS_SCHIP_SPRITE.height

    hoogste_terrein = max(SPACE_INVADERS_TERREIN)
    laagste_terrein = min(SPACE_INVADERS_TERREIN)
    schip_y_max = HEIGHT - laagste_terrein - schip_hoogte - 1
    schip_y_min = HEIGHT - hoogste_terrein - schip_hoogte - 9
    if schip_y_min > schip_y_max:
        schip_y_min = schip_y_max - 6

    schip_x = float((WIDTH - schip_breedte) // 2)
    schip_y = float(schip_y_max)
    schip_vx = random.choice([-0.8, -0.5, 0.5, 0.8])
    schip_vy = 0.0

    invaders = []
    projectielen = []
    explosies = []

    hits = 0
    laatste_schot = 0.0
    volgende_spawn = 0.0

    def spawn_invader():
        sprite = random.choice(INVADERS_SPRITES)
        x = random.randint(0, max(0, WIDTH - sprite.width))
        invaders.append({
            "x": float(x),
            "y": -float(sprite.height),
            "vy": random.uniform(0.15, 0.35),
            "sprite": sprite,
        })

    while animatie_nog_actief(mijn_id) and hits < SPACE_INVADERS_HITS_NODIG:
        nu = time.time()

        # Spawn new invaders
        if nu >= volgende_spawn and len(invaders) < SPACE_INVADERS_MAX_TEGELIJK:
            spawn_invader()
            volgende_spawn = nu + random.uniform(0.7, 1.6)

        # Ship: random course changes, left/right and a little up/down
        if random.random() < 0.04:
            schip_vx = random.choice([-1.0, -0.7, -0.4, 0.4, 0.7, 1.0])
        if random.random() < 0.06:
            schip_vy = random.choice([-0.4, -0.2, 0.0, 0.2, 0.4])

        schip_x += schip_vx
        schip_y += schip_vy

        if schip_x <= 0:
            schip_x = 0.0
            schip_vx = abs(schip_vx)
        elif schip_x >= WIDTH - schip_breedte:
            schip_x = float(WIDTH - schip_breedte)
            schip_vx = -abs(schip_vx)

        if schip_y <= schip_y_min:
            schip_y = float(schip_y_min)
            schip_vy = abs(schip_vy)
        elif schip_y >= schip_y_max:
            schip_y = float(schip_y_max)
            schip_vy = -abs(schip_vy)

        # Shooting (automatic, at a fixed interval)
        if nu - laatste_schot > SPACE_INVADERS_VUUR_INTERVAL:
            projectielen.append({"x": schip_x + schip_breedte / 2, "y": schip_y})
            laatste_schot = nu

        # Let projectiles rise
        for p in projectielen:
            p["y"] -= 2.2
        projectielen = [p for p in projectielen if p["y"] > 0]

        # Let invaders descend
        for inv in invaders:
            inv["y"] += inv["vy"]

        # Invaders that reach the ground without being hit simply disappear
        invaders = [
            inv for inv in invaders
            if inv["y"] + inv["sprite"].height < HEIGHT - terrein_hoogte_op(inv["x"] + inv["sprite"].width / 2)
        ]

        # Collisions: projectile hits invader
        geraakte = set()
        nog_actieve_projectielen = []
        for p in projectielen:
            raak = None
            for idx, inv in enumerate(invaders):
                if idx in geraakte:
                    continue
                if (inv["x"] <= p["x"] <= inv["x"] + inv["sprite"].width and
                        inv["y"] <= p["y"] <= inv["y"] + inv["sprite"].height):
                    raak = idx
                    break
            if raak is not None:
                inv = invaders[raak]
                explosies.append({
                    "x": inv["x"] + inv["sprite"].width / 2,
                    "y": inv["y"] + inv["sprite"].height / 2,
                    "stap": 0,
                })
                geraakte.add(raak)
                hits += 1
            else:
                nog_actieve_projectielen.append(p)
        projectielen = nog_actieve_projectielen
        if geraakte:
            invaders = [inv for idx, inv in enumerate(invaders) if idx not in geraakte]

        # Build and send frame
        canvas = maak_canvas()
        teken_terrein(canvas)
        for inv in invaders:
            plak_icoon(canvas, inv["sprite"], int(inv["x"]), int(inv["y"]))
        for p in projectielen:
            zet_pixel(canvas, int(p["x"]), int(p["y"]), (255, 255, 0))
            zet_pixel(canvas, int(p["x"]), int(p["y"]) + 1, (255, 200, 0))
        plak_icoon(canvas, INVADERS_SCHIP_SPRITE, int(schip_x), int(schip_y))

        nog_actieve_explosies = []
        for ex in explosies:
            teken_kleine_explosie(canvas, int(ex["x"]), int(ex["y"]), ex["stap"])
            ex["stap"] += 1
            if ex["stap"] < 4:
                nog_actieve_explosies.append(ex)
        explosies = nog_actieve_explosies

        stuur_via_ddp(canvas)
        time.sleep(SPACE_INVADERS_INTERVAL)

    # Required hits reached: a real fireball explosion from the ship that fills the whole screen
    if animatie_nog_actief(mijn_id) and hits >= SPACE_INVADERS_HITS_NODIG:
        explosie_rng = random.Random()
        oorsprong_x = schip_x + schip_breedte / 2
        oorsprong_y = schip_y + schip_hoogte / 2

        # Choose the radius so the fireball eventually reaches the farthest corner of the matrix
        hoeken = [(0, 0), (WIDTH, 0), (0, HEIGHT), (WIDTH, HEIGHT)]
        max_afstand = max(math.hypot(oorsprong_x - hx, oorsprong_y - hy) for hx, hy in hoeken)

        TOTAAL_EXPLOSIE_STAPPEN = SPACE_INVADERS_EXPLOSIE_STAPPEN
        for stap in range(TOTAAL_EXPLOSIE_STAPPEN):
            if not animatie_nog_actief(mijn_id):
                return
            canvas = maak_canvas()
            if stap == 0:
                teken_vuurbal(canvas, oorsprong_x, oorsprong_y, 0, explosie_rng, flits=True)
            else:
                progress = stap / (TOTAAL_EXPLOSIE_STAPPEN - 1)
                radius = max_afstand * min(1.0, progress * 1.25)
                verdunning = 1.0
                if stap >= TOTAAL_EXPLOSIE_STAPPEN - 3:
                    verdunning = 1.0 + (stap - (TOTAAL_EXPLOSIE_STAPPEN - 3)) * 0.6
                teken_vuurbal(canvas, oorsprong_x, oorsprong_y, radius, explosie_rng, verdunning=verdunning)
            stuur_via_ddp(canvas)
            time.sleep(SPACE_INVADERS_EXPLOSIE_INTERVAL)

    leeg_canvas = maak_canvas()
    stuur_via_ddp(leeg_canvas)
    animatie_klaar(mijn_id)
    print(f"Space Invaders animation finished after {hits} hits.")

# ============================================================
# Pac-Man maze simulation
# ============================================================

MAZE_COLS, MAZE_ROWS, MAZE_CEL = 16, 8, 4  # 16*4=64, 8*4=32 -> fills the whole matrix

MAZE_MUREN = set()
for _c in range(MAZE_COLS):
    MAZE_MUREN.add((0, _c)); MAZE_MUREN.add((MAZE_ROWS - 1, _c))
for _r in range(MAZE_ROWS):
    MAZE_MUREN.add((_r, 0)); MAZE_MUREN.add((_r, MAZE_COLS - 1))
for _p in [(2, 3), (2, 7), (2, 8), (2, 12), (4, 3), (4, 12), (5, 7), (5, 8)]:
    MAZE_MUREN.add(_p)

MAZE_MUUR_KLEUR = (30, 60, 200)
MAZE_KADER_KLEUR = (255, 140, 0)
MAZE_KADER_DIKTE = 2
MAZE_PELLET_KLEUR = (255, 235, 150)

def maze_cel_naar_px(rij, kol):
    """Return the pixel centre (x, y) of a maze cell."""
    return (kol * MAZE_CEL + MAZE_CEL / 2, rij * MAZE_CEL + MAZE_CEL / 2)

def maze_buren(rij, kol):
    """Return the possible move directions (dr, dc) from a cell (no walls)."""
    mogelijk = []
    for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
        nr, nc = rij + dr, kol + dc
        if 0 <= nr < MAZE_ROWS and 0 <= nc < MAZE_COLS and (nr, nc) not in MAZE_MUREN:
            mogelijk.append((dr, dc))
    return mogelijk

def teken_maze(canvas, pellets):
    """Draw the maze: frame, inner pillars and remaining pellets."""
    draw = ImageDraw.Draw(canvas)

    # Outer border (frame): thin and orange, independent of the thickness of the inner pillars
    for laag in range(MAZE_KADER_DIKTE):
        draw.rectangle([laag, laag, WIDTH - 1 - laag, HEIGHT - 1 - laag], outline=MAZE_KADER_KLEUR)

    # Inner pillars: full cell, blue
    for (r, c) in MAZE_MUREN:
        if r in (0, MAZE_ROWS - 1) or c in (0, MAZE_COLS - 1):
            continue  # these are border cells; they were already drawn as the frame above
        draw.rectangle([c * MAZE_CEL, r * MAZE_CEL, c * MAZE_CEL + MAZE_CEL - 1, r * MAZE_CEL + MAZE_CEL - 1],
                        fill=MAZE_MUUR_KLEUR)

    for (r, c) in pellets:
        x, y = maze_cel_naar_px(r, c)
        zet_pixel(canvas, int(x), int(y), MAZE_PELLET_KLEUR)

def maze_nieuwe_entiteit(rij, kol):
    """Create a new maze entity (Pac-Man or ghost) at a cell with a random starting direction."""
    richting = random.choice(maze_buren(rij, kol))
    return {
        "rij": rij, "kol": kol,
        "volgende_rij": rij + richting[0], "volgende_kol": kol + richting[1],
        "voortgang": 0.0,
        "vorige_richting": richting,
    }

def maze_kies_volgende_richting(entiteit, is_pacman, geesten_posities):
    """Choose the next direction: never reverse unless forced; Pac-Man leans towards the ghosts."""
    mogelijk = maze_buren(entiteit["rij"], entiteit["kol"])
    omgekeerd = (-entiteit["vorige_richting"][0], -entiteit["vorige_richting"][1])
    kandidaten = [d for d in mogelijk if d != omgekeerd] or mogelijk

    if is_pacman and geesten_posities and random.random() < 0.7:
        def score(d):
            nr, nc = entiteit["rij"] + d[0], entiteit["kol"] + d[1]
            return min(abs(nr - gr) + abs(nc - gc) for gr, gc in geesten_posities)
        return min(kandidaten, key=score)
    return random.choice(kandidaten)

def maze_update_entiteit(entiteit, is_pacman, geesten_posities, pellets):
    """Advance an entity along its path and return its current pixel position (px, py)."""
    entiteit["voortgang"] += PACMAN_SNELHEID
    if entiteit["voortgang"] >= 1.0:
        entiteit["rij"], entiteit["kol"] = entiteit["volgende_rij"], entiteit["volgende_kol"]
        entiteit["voortgang"] = 0.0
        if is_pacman:
            pellets.discard((entiteit["rij"], entiteit["kol"]))
        richting = maze_kies_volgende_richting(entiteit, is_pacman, geesten_posities)
        entiteit["volgende_rij"] = entiteit["rij"] + richting[0]
        entiteit["volgende_kol"] = entiteit["kol"] + richting[1]
        entiteit["vorige_richting"] = richting

    x1, y1 = maze_cel_naar_px(entiteit["rij"], entiteit["kol"])
    x2, y2 = maze_cel_naar_px(entiteit["volgende_rij"], entiteit["volgende_kol"])
    px = x1 + (x2 - x1) * entiteit["voortgang"]
    py = y1 + (y2 - y1) * entiteit["voortgang"]
    return px, py

def maze_verre_cel(vanaf_rij, vanaf_kol):
    """Pick an open cell as far as possible from the given position (used to respawn ghosts)."""
    open_cellen = [(r, c) for r in range(MAZE_ROWS) for c in range(MAZE_COLS) if (r, c) not in MAZE_MUREN]
    open_cellen.sort(key=lambda rc: -(abs(rc[0] - vanaf_rij) + abs(rc[1] - vanaf_kol)))
    return random.choice(open_cellen[:8])

def start_pacman_simulatie():
    """Animation 2: Pac-Man plays in a maze. After PACMAN_KILLS_NODIG ghosts are caught a big
    Pac-Man runs across the screen (right to left), followed by a big explosion."""
    mijn_id = nieuwe_animatie_id()

    if not GHOST_SPRITES or PACMAN_MAZE_FRAMES[0] is None:
        print("Cannot start Pac-Man simulation: sprites missing.")
        animatie_klaar(mijn_id)
        return

    pellets = set((r, c) for r in range(MAZE_ROWS) for c in range(MAZE_COLS) if (r, c) not in MAZE_MUREN)

    pacman = maze_nieuwe_entiteit(MAZE_ROWS // 2, MAZE_COLS // 2)
    pellets.discard((pacman["rij"], pacman["kol"]))

    start_hoeken = [(1, 1), (1, MAZE_COLS - 2), (MAZE_ROWS - 2, 1), (MAZE_ROWS - 2, MAZE_COLS - 2)]
    geesten = []
    for i, sprite in enumerate(GHOST_SPRITES):
        hr, hc = start_hoeken[i % len(start_hoeken)]
        g = maze_nieuwe_entiteit(hr, hc)
        g["sprite"] = sprite
        geesten.append(g)

    kills = 0
    explosies = []
    mond_open = True
    mond_teller = 0

    while animatie_nog_actief(mijn_id) and kills < PACMAN_KILLS_NODIG:
        geesten_posities = [(g["rij"], g["kol"]) for g in geesten]
        pac_px, pac_py = maze_update_entiteit(pacman, True, geesten_posities, pellets)

        for g in geesten:
            g["px"], g["py"] = maze_update_entiteit(g, False, [], pellets)

        # Collision: Pac-Man catches a ghost
        gevangen = []
        for g in geesten:
            if math.hypot(pac_px - g["px"], pac_py - g["py"]) < 3.0:
                gevangen.append(g)
        for g in gevangen:
            explosies.append({"x": g["px"], "y": g["py"], "stap": 0})
            kills += 1
            verre_rij, verre_kol = maze_verre_cel(pacman["rij"], pacman["kol"])
            nieuw = maze_nieuwe_entiteit(verre_rij, verre_kol)
            nieuw["sprite"] = g["sprite"]
            nieuw["px"], nieuw["py"] = maze_cel_naar_px(verre_rij, verre_kol)
            geesten.remove(g)
            geesten.append(nieuw)
            if kills >= PACMAN_KILLS_NODIG:
                break

        # Build frame
        canvas = maak_canvas()
        teken_maze(canvas, pellets)
        for g in geesten:
            plak_icoon(canvas, g["sprite"], int(g["px"] - g["sprite"].width / 2), int(g["py"] - g["sprite"].height / 2))

        mond_teller += 1
        if mond_teller % 3 == 0:
            mond_open = not mond_open
        pac_sprite = PACMAN_MAZE_FRAMES[0] if mond_open else PACMAN_MAZE_FRAMES[1]
        plak_icoon(canvas, pac_sprite, int(pac_px - pac_sprite.width / 2), int(pac_py - pac_sprite.height / 2))

        nog_actief = []
        for ex in explosies:
            teken_kleine_explosie(canvas, int(ex["x"]), int(ex["y"]), ex["stap"])
            ex["stap"] += 1
            if ex["stap"] < 4:
                nog_actief.append(ex)
        explosies = nog_actief

        stuur_via_ddp(canvas)
        time.sleep(PACMAN_INTERVAL)

    if not animatie_nog_actief(mijn_id):
        return

    # Finale, phase 1: the big Pac-Man runs across the whole screen, right to left
    groot_y = (HEIGHT - PACMAN_GROOT_FRAMES[0].height) // 2
    groot_x = float(WIDTH)
    frame_teller = 0
    while groot_x > -PACMAN_GROOT_FRAMES[0].width:
        if not animatie_nog_actief(mijn_id):
            return
        canvas = maak_canvas()
        sprite = PACMAN_GROOT_FRAMES[0] if (frame_teller // 3) % 2 == 0 else PACMAN_GROOT_FRAMES[1]
        plak_icoon(canvas, sprite, int(groot_x), groot_y)
        stuur_via_ddp(canvas)
        groot_x -= PACMAN_GROOT_STAP
        frame_teller += 1
        time.sleep(PACMAN_GROOT_INTERVAL)

    # Finale, phase 2: big fireball explosion (same effect as in Space Invaders)
    explosie_rng = random.Random()
    oorsprong_x, oorsprong_y = WIDTH / 2, HEIGHT / 2
    hoeken = [(0, 0), (WIDTH, 0), (0, HEIGHT), (WIDTH, HEIGHT)]
    max_afstand = max(math.hypot(oorsprong_x - hx, oorsprong_y - hy) for hx, hy in hoeken)

    for stap in range(PACMAN_EXPLOSIE_STAPPEN):
        if not animatie_nog_actief(mijn_id):
            return
        canvas = maak_canvas()
        if stap == 0:
            teken_vuurbal(canvas, oorsprong_x, oorsprong_y, 0, explosie_rng, flits=True)
        else:
            progress = stap / (PACMAN_EXPLOSIE_STAPPEN - 1)
            radius = max_afstand * min(1.0, progress * 1.25)
            verdunning = 1.0
            if stap >= PACMAN_EXPLOSIE_STAPPEN - 3:
                verdunning = 1.0 + (stap - (PACMAN_EXPLOSIE_STAPPEN - 3)) * 0.6
            teken_vuurbal(canvas, oorsprong_x, oorsprong_y, radius, explosie_rng, verdunning=verdunning)
        stuur_via_ddp(canvas)
        time.sleep(PACMAN_EXPLOSIE_INTERVAL)

    leeg_canvas = maak_canvas()
    stuur_via_ddp(leeg_canvas)
    animatie_klaar(mijn_id)
    print(f"Pac-Man simulation finished after {kills} ghosts caught.")

# ============================================================
# MQTT logic
# ============================================================

# Latest values received over MQTT (the weather screen is shown once both are known)
huidige_staat = {
    "weer_code": None,
    "temperatuur": None,
}

def on_connect(client, userdata, flags, rc):
    """MQTT callback: subscribe to all topics after a successful connection."""
    if rc == 0:
        print("Connected to MQTT broker.")
        client.subscribe(TOPIC_WEER_ICOON)
        client.subscribe(TOPIC_TEMPERATUUR)
        client.subscribe(TOPIC_ANIMATIE)
        client.subscribe(TOPIC_TEKST)
        print("Subscribed to all topics.")

def on_message(client, userdata, msg):
    """MQTT callback: handle weather type, temperature, animation trigger and scroll-text messages."""
    payload = msg.payload.decode("utf-8").strip().lower() # .lower() to make matching case-insensitive

    if msg.topic == TOPIC_ANIMATIE:
        if payload == "1":
            t = threading.Thread(target=start_mario_jacht, daemon=True)
            t.start()
        elif payload == "2":
            t = threading.Thread(target=start_pacman_simulatie, daemon=True)
            t.start()
        elif payload == "3":
            weer = huidige_staat["weer_code"] or "clear"
            temp = huidige_staat["temperatuur"] if huidige_staat["temperatuur"] is not None else 20.0
            t = threading.Thread(target=start_destructie_animatie, args=(weer, temp), daemon=True)
            t.start()
        elif payload == "4":
            t = threading.Thread(target=start_space_invaders, daemon=True)
            t.start()
        elif payload == "5":
            t = threading.Thread(target=start_breakout, daemon=True)
            t.start()
        elif payload == "6":
            t = threading.Thread(target=start_pong, daemon=True)
            t.start()
        elif payload == "7":
            t = threading.Thread(target=start_snake, daemon=True)
            t.start()
        elif payload == "8":
            t = threading.Thread(target=start_tetris, daemon=True)
            t.start()
        elif payload == "9":
            t = threading.Thread(target=start_missile_command, daemon=True)
            t.start()
    elif msg.topic == TOPIC_TEKST:
        if payload:
            t = threading.Thread(target=start_scroll_tekst, args=(payload,), daemon=True)
            t.start()
    elif msg.topic == TOPIC_WEER_ICOON:
        # Translation table: Home Assistant / external MQTT weather types -> this script's icon keys
        print(f"Received MQTT weather type: '{payload}'")
        
        weer_vertalingen = {
            "sunny": "clear",
            "clear-night": "clear",
            "partly-cloudy-day": "partlycloudy",
            "partly-cloudy-night": "partlycloudy",
            "cloudy": "cloudy",
            "fog": "fog",
            "rainy": "rainy",
            "pouring": "rainy",
            "light-rain": "rainy",
            "snowy": "snowy",
            "snowy-rainy": "snowy-rainy",
            "lightning": "lightning",
            "lightning-rainy": "partlycloudy-lightning",
            "windy": "windy",      # Map windy to the key 'windy'
            "breezy": "windy"      # If breezy comes in as well, map it to 'windy' too
        }
        
        # Translate the payload if it is in the table, otherwise keep the original payload
        vertaalde_code = weer_vertalingen.get(payload, payload)
        print(f"After translation: '{vertaalde_code}'")
        
        if vertaalde_code in WEER_ICOON_BESTANDEN:
            huidige_staat["weer_code"] = vertaalde_code
        else:
            print(f"Unknown weather type received via MQTT: '{payload}' (translated to: '{vertaalde_code}')")

    elif msg.topic == TOPIC_TEMPERATUUR:
        try:
            huidige_staat["temperatuur"] = float(payload)
        except ValueError:
            pass

    if huidige_staat["weer_code"] is not None and huidige_staat["temperatuur"] is not None and not animatie_actief.is_set():
        toon_weerscherm(huidige_staat["weer_code"], huidige_staat["temperatuur"])

def main():
    """Check the configuration, connect to the MQTT broker and handle messages until Ctrl+C."""
    if not WLED_IP or not MQTT_BROKER:
        sys.exit("Please fill in WLED_IP and MQTT_BROKER in the CONFIG section at the top of this script.")
    # paho-mqtt 2.x needs an explicit callback API version; 1.x (e.g. the Debian package) does not have it
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
    except AttributeError:
        client = mqtt.Client()
    if MQTT_USERNAME:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

    client.on_connect = on_connect
    client.on_message = on_message

    client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
    print("Waiting for MQTT messages... (Ctrl+C to stop)")
    try:
        client.loop_forever()
    except KeyboardInterrupt:
        client.disconnect()

if __name__ == "__main__":
    main()
