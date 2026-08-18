# Asset Pipeline — AI-Powered Content Generation for MilSim

## Overview

This document defines how we create graphics, audio, music, and speech for the
military simulation, combining OpenRA's asset system with AI generation tools.

---

## 1. OpenRA Asset Formats (What the Engine Expects)

### Sprites / Graphics

OpenRA accepts two sprite formats:

| Format | Description | Our Choice |
|--------|-------------|------------|
| **Westwood .SHP** | Legacy palette-indexed format, 256 colors | No — legacy |
| **PNG sprite sheets** | Standard PNG with `FrameSize` and `FrameAmount` metadata | **Yes** |

OpenHV (the most modern OpenRA mod) uses PNG exclusively. We do the same.

**Sprite sheet structure:**
- Horizontal strip of frames, each frame same pixel dimensions
- Transparent background (alpha channel)
- Metadata embedded or defined in `sequences.yaml`

**Sequence YAML maps sprites to animations:**
```yaml
USRifleSquad:
  idle:
    Filename: nato_symbols/infantry_blufor.png
    Start: 0
    Length: 1
    Facings: 1
  selected:
    Filename: nato_symbols/infantry_blufor_selected.png
    Start: 0
    Length: 4
    Tick: 200
  icon:
    Filename: nato_symbols/infantry_blufor_icon.png
    Start: 0
    Length: 1
```

Key sequence properties:
- `Start` — starting frame index
- `Length` — number of frames (`*` = all remaining)
- `Facings` — directional variants (1 for top-down symbols, 8/16/32 for directional sprites)
- `Tick` — animation speed (ms per frame, default 40)
- `Offset` — pixel offset for rendering
- `BlendMode` — Alpha, Additive, Screen, etc.
- `ShadowStart` — frame index for shadow sprites

### Terrain / Tilesets

- Defined in YAML under `tilesets/`
- Tiles are PNG or TMP files
- Each tile maps to a terrain type (Clear, Road, Water, Forest, Urban, etc.)
- OpenRA PR #18728 introduced `ITerrainLoader` — allows custom terrain systems

### Audio

| Format | Use |
|--------|-----|
| **WAV (IMA ADPCM)** | Sound effects, weapon sounds — preferred |
| **OGG Vorbis** | Music, longer audio — smaller file size |

Audio is mapped in YAML files:
- `music.yaml` — background tracks
- `notifications.yaml` — UI alerts, commander voice lines
- `voices.yaml` — unit speech (Select, Move, Attack, Die)
- Weapon sounds via `Report:` property in `weapons.yaml`

### Tools OpenRA Provides

- `OpenRA.Utility --shp` — combine PNGs into SHP
- `OpenRA.Utility --png` — convert SHP to PNG
- `OpenRA.Utility --check-yaml` — validate YAML
- `OpenRA.Utility --check-missing-sprites` — find missing references
- Built-in **Map Editor** (terrain painting, actor placement, Lua scripting)
- Built-in **Asset Browser** for inspecting loaded sprites/sounds
- **OpenRA Mod SDK** (`github.com/OpenRA/OpenRAModSDK`) — mod development template

---

## 2. AI-Powered Asset Generation Pipeline

### Architecture

```
BUILD-TIME PIPELINE (Python + ffmpeg + Pillow)

Claude API --> Generates text:
(Narrative)    - Unit descriptions for sprite prompts
               - Dialogue scripts for TTS
               - Music mood/style descriptions
               - Scenario narratives
    |
    |-> milsymbol (JS) --> NATO unit symbols (SVG->PNG)
    |
    |-> Stable Diffusion --> Terrain textures, effects
    |   (self-hosted)       Unit concept art
    |
    |-> AIVA / MusicGen --> Background music (OGG)
    |
    |-> ElevenLabs TTS --> Radio voice lines (WAV)
    |   + ffmpeg radio fx
    |
    |-> ElevenLabs SFX --> Weapon/vehicle sounds (WAV)
    |   + Freesound.org
    |
    |-> Gaea / SRTM data --> Terrain heightmaps

All outputs -> Asset cache (content-addressed)
            -> milsim/mod/sprites/, bits/, music/


RUNTIME PIPELINE (In-game, on demand)

Claude API --> Dynamic OPFOR decisions, MSEL injects
    |
    |-> ElevenLabs Streaming TTS --> Live radio chatter
         (~300ms latency)            with radio FX

Local cache --> Pre-generated sprites, music, SFX
```

---

## 3. Graphics Generation

### 3.1 NATO Military Symbols (Primary Unit Graphics)

For a military simulation, **NATO APP-6 standard symbols** are the correct
representation — not pixel art soldiers or tanks.

**Tool: milsymbol (open source, MIT license)**
- GitHub: `github.com/spatialillusions/milsymbol`
- Generates MIL-STD-2525 / APP-6 compliant symbols
- Output: SVG (convert to PNG with Pillow/rsvg for OpenRA)
- Supports: unit size, affiliation (friendly/hostile/neutral/unknown),
  echelon, equipment type, status, modifiers

**Pipeline:**
```
Unit YAML definition
  -> Claude parses unit type, echelon, branch, faction
  -> milsymbol generates SVG with correct symbology
  -> rsvg-convert / Pillow converts SVG -> PNG (64x64 or 128x128)
  -> Pillow composites into sprite sheet (idle, selected, damaged states)
  -> Output: milsim/mod/sprites/nato_symbols/<unit_type>.png
  -> sequences.yaml auto-generated
```

**Symbol variations per unit:**
| State | Visual |
|-------|--------|
| Normal | Standard NATO symbol |
| Selected | Yellow highlight border |
| Damaged | Red slash overlay |
| Suppressed | Yellow "S" modifier |
| Low Ammo | Black dot modifier |
| Moving | Direction arrow overlay |
| Destroyed | Red X overlay |

**Claude's role:** Given a unit definition YAML, Claude generates the correct
milsymbol parameters:
```json
{
  "sidc": "SFGPUCI----D",
  "size": 64,
  "affiliation": "friendly",
  "echelon": "squad",
  "type": "infantry",
  "modifiers": {
    "uniqueDesignation": "1-1",
    "higherFormation": "A/2-7 IN"
  }
}
```

### 3.2 Terrain Textures

**Tool: Stable Diffusion (self-hosted) with tiling ControlNet**

Generate seamless terrain tiles for each terrain type:
- Temperate grassland, forest, farmland, roads, rivers
- Desert sand, rock, wadis
- Arctic snow, ice, frozen rivers
- Urban concrete, buildings (top-down), rubble

**Pipeline:**
```
Terrain type definition
  -> SD prompt: "seamless top-down satellite view, temperate grassland,
     game tile, 64x64 pixels, no shadows"
  -> SD generates 8-16 tile variants per terrain type
  -> Pillow arranges into tileset PNG
  -> TilesetBuilder creates tileset YAML
  -> Output: milsim/mod/maps/tilesets/<terrain>.png + .yaml
```

### 3.3 Effect Sprites (Explosions, Smoke, Tracers)

**Tool: Stable Diffusion or sprite sheet libraries**

- Explosion sequences (8-16 frames)
- Smoke plumes (sustained, drifting)
- Muzzle flashes
- Tracer rounds
- Impact effects (dirt, sparks, water splash)

For prototyping, use open-licensed sprite packs from OpenGameArt.org or
Kenney.nl, then replace with AI-generated or custom art later.

### 3.4 Concept Art & UI

**Tool: Midjourney or DALL-E 3** for high-quality illustrations:
- Loading screens
- Faction insignia
- Scenario briefing illustrations
- UI background art
- Commander portraits (for briefings/AAR)

---

## 4. Audio Generation

### 4.1 Music

**Primary tool: AIVA Pro (EUR49/month) — full copyright ownership**
**Fallback: MusicGen (Meta, open source, self-hosted)**

Music categories needed:

| Track Type | Mood | Duration | Loop |
|------------|------|----------|------|
| Main menu | Dramatic orchestral, military | 3-4 min | Yes |
| Planning phase | Tense, contemplative | 2-3 min | Yes |
| Execution phase (low intensity) | Suspenseful, building | 2-3 min | Yes |
| Execution phase (combat) | Intense, percussive | 2-3 min | Yes |
| Victory | Triumphant fanfare | 30 sec | No |
| Defeat | Somber, reflective | 30 sec | No |
| AAR / Debrief | Neutral, analytical | 2-3 min | Yes |
| Briefing | Serious, focused | 2 min | Yes |

**Pipeline:**
```
Claude generates mood description per track type
  -> AIVA Pro generates orchestral composition
  -> ffmpeg trims to loop point, crossfades endpoints
  -> ffmpeg converts to OGG Vorbis (quality 6)
  -> Output: milsim/mod/music/<track_name>.ogg
  -> music.yaml auto-generated
```

### 4.2 Sound Effects

**Primary: ElevenLabs SFX API ($5-22/month)**
**Supplement: Freesound.org (CC0/CC-BY) for real weapon recordings**
**Fallback: AudioCraft/AudioGen (Meta, open source)**

SFX categories:

| Category | Examples | Source Preference |
|----------|----------|-------------------|
| Small arms | M4 burst, AK-74, MG fire | Freesound.org (real recordings) |
| Heavy weapons | Tank gun, autocannon, RPG | Freesound.org + ElevenLabs |
| Explosions | Artillery impact, IED, grenade | ElevenLabs SFX |
| Vehicles | Tank engine, truck, helicopter | ElevenLabs SFX |
| Ambient | Wind, rain, birds, urban noise | ElevenLabs SFX |
| UI | Click, alert, phase change | ElevenLabs SFX |

**For realistic weapon sounds, Freesound.org is superior to AI generation.**
AI-generated weapon SFX are ~70-80% quality vs real recordings at ~95%.
Use AI for ambient, UI, and filler sounds; use real recordings for hero sounds.

### 4.3 Voice / Speech — Military Radio Communications

This is where the LLM pipeline really shines. Claude generates the dialogue
text, ElevenLabs converts to speech, then ffmpeg applies radio effects.

**Primary: ElevenLabs API ($22-99/month)**
**Fallback: Coqui TTS XTTS v2 (open source, self-hosted)**

**Voice roster:**

| Character | Voice Profile | Use |
|-----------|--------------|-----|
| CO (Commanding Officer) | Male, authoritative, calm | OPORD briefings, orders |
| Platoon Leader (1st Plt) | Male, younger, urgent | Contact reports, SITREPs |
| Platoon Leader (2nd Plt) | Female, professional | Movement reports, requests |
| FSO (Fire Support) | Male, deliberate, precise | Call-for-fire, fire missions |
| S2 (Intel) | Female, analytical | Intel reports, warnings |
| OPFOR Commander | Male, accented (Russian/Arabic) | Intercepted comms |
| Radio Operator | Male/Female, procedural | Relay messages |
| Civilian | Various | ROE scenarios |

**Radio effect pipeline (ffmpeg):**
```bash
# Step 1: Generate clean speech via ElevenLabs API
# Step 2: Apply military radio effects
ffmpeg -i clean_speech.wav \
  -af "highpass=f=300,lowpass=f=3400,\
       acompressor=threshold=-20dB:ratio=4,\
       volume=0.9" \
  -ar 22050 \
  radio_speech.wav

# Step 3: Mix in radio static/squelch from library
ffmpeg -i radio_speech.wav -i squelch_start.wav -i squelch_end.wav \
  -filter_complex "[1][0][2]concat=n=3:v=0:a=1" \
  final_radio.wav

# Step 4: Convert to game format
ffmpeg -i final_radio.wav -acodec adpcm_ima_wav radio_final.wav
```

**Pre-generated voice library (build-time):**
Common phrases that don't need runtime generation:

```yaml
CommonPhrases:
  Acknowledgments:
    - "Roger, out."
    - "Copy, wilco."
    - "Acknowledged, moving now."
    - "Roger, standing by."
  Contact:
    - "Contact front!"
    - "Contact, enemy infantry!"
    - "Taking fire! Taking fire!"
    - "RPG! RPG!"
  Movement:
    - "Moving to waypoint Alpha."
    - "Set, in position."
    - "Displacing now."
    - "Route clear, proceeding."
  Fire Support:
    - "Shot, over."
    - "Splash, over."
    - "Rounds complete."
    - "Fire mission, adjust fire, over."
  Casualties:
    - "Man down! Medic!"
    - "We have one urgent, one priority."
    - "Request MEDEVAC, over."
  Status:
    - "Ammo status amber."
    - "Fuel status red."
    - "All elements set."
```

**Runtime voice generation (in-game):**
For dynamic content (unique SITREPs, OPFOR intercepts, LLM-generated dialogue):
```
Claude generates dialogue text
  -> ElevenLabs Streaming TTS API (~300ms latency)
  -> Client-side Web Audio API applies radio filter in real-time
  -> Plays through game audio
```

---

## 5. Terrain / Map Generation

### 5.1 Real-World Terrain Import

**Source: SRTM (Shuttle Radar Topography Mission) — free, 30m resolution globally**

```
User specifies real-world location (e.g., "Suwalki Gap, Poland-Lithuania border")
  -> Download SRTM elevation data via OpenTopography API
  -> Convert to 16-bit grayscale heightmap PNG
  -> Claude analyzes the terrain and suggests:
    - Key terrain features for gameplay
    - Chokepoints, high ground, dead ground
    - Road network overlay
    - Suggested objective locations
  -> Import into OpenRA map editor
  -> Apply tileset based on real-world land cover data
```

### 5.2 Procedural Terrain Generation

**Tool: Gaea (free Community edition) or World Machine**

For fictional but realistic maps:
```
Claude generates terrain description based on scenario:
  "Rolling European farmland with scattered tree lines,
   a river running east-west with two bridge crossings,
   a small village on the north bank, high ground to the NE"
  -> Gaea generates heightmap procedurally
  -> Export as 16-bit PNG heightmap
  -> Import into OpenRA map editor
  -> Place features (buildings, roads, bridges) manually or via script
```

### 5.3 AI-Assisted Map Layout

Claude can design the tactical layout of maps:
```
Prompt: "Design a company-level attack map. The defender has:
  - High ground in the NE with good observation
  - A river obstacle running E-W
  - Two crossing points (bridge and ford)
  - A village on the far bank
  The attacker approaches from the south.
  Design terrain that creates interesting tactical choices."

Claude outputs:
  - Grid coordinates for key features
  - Terrain type assignments per area
  - Suggested unit placement for balanced gameplay
  - Key terrain analysis (OAKOC: Observation, Avenues of Approach,
    Key terrain, Obstacles, Cover & Concealment)
```

---

## 6. Content-Addressed Asset Cache

All generated assets are cached by prompt hash to avoid regeneration:

```
cache/
  sprites/
    a3f8c2e1.png          # NATO infantry symbol (BLUFOR, squad)
    manifest.json          # Maps unit_type -> cache file
  audio/
    voice/
      b7d4e9f2.wav        # "Contact front!" -- Voice: PL1
      manifest.json
    sfx/
      c1a2b3c4.wav        # M4 burst, outdoor
      manifest.json
    music/
      d5e6f7a8.ogg        # Planning phase -- tense orchestral
      manifest.json
  terrain/
    e9f0a1b2.png          # Temperate grassland tile variant 3
    manifest.json
  cache_index.json         # Master index: prompt_hash -> file
```

**Cache key formula:**
```python
cache_key = sha256(f"{tool}:{prompt}:{parameters}:{version}").hexdigest()[:12]
```

**Fallback assets:** Every generated asset category has hand-made fallbacks
so the game never blocks on an API call. The fallback set is committed to git.

---

## 7. Build Pipeline Script

The asset generation pipeline runs as a Python script during build:

```
milsim/
  pipeline/
    generate_assets.py     # Main pipeline orchestrator
    generate_symbols.py    # NATO symbol generation (milsymbol)
    generate_terrain.py    # Terrain tile generation (SD)
    generate_music.py      # Music generation (AIVA/MusicGen)
    generate_sfx.py        # SFX generation (ElevenLabs)
    generate_voices.py     # Voice line generation (ElevenLabs)
    radio_fx.py            # ffmpeg radio effect processing
    sprite_sheet.py        # Sprite sheet assembly (Pillow)
    cache.py               # Content-addressed cache manager
    requirements.txt       # Python dependencies
    config.yaml            # API keys (env vars), model settings
```

**Usage:**
```bash
# Generate all assets (uses cache, only regenerates missing)
python pipeline/generate_assets.py --all

# Generate only specific categories
python pipeline/generate_assets.py --symbols --voices

# Force regeneration (ignore cache)
python pipeline/generate_assets.py --all --force

# Dry run (show what would be generated)
python pipeline/generate_assets.py --all --dry-run
```

---

## 8. Tool Stack Summary

| Category | Primary Tool | Cost | License |
|----------|-------------|------|--------|
| NATO Symbols | milsymbol | Free | MIT |
| Unit Sprites | milsymbol + Pillow | Free | MIT |
| Terrain Textures | Stable Diffusion (self-hosted) | Free (GPU required) | Apache 2.0 |
| Concept Art | Midjourney / DALL-E 3 | $10-60/mo | Commercial |
| Music | AIVA Pro | EUR49/mo | Full copyright |
| Music (fallback) | MusicGen (Meta) | Free | MIT/CC |
| SFX (weapons) | Freesound.org | Free | CC0/CC-BY |
| SFX (ambient) | ElevenLabs SFX | $5-22/mo | Commercial |
| Voice / TTS | ElevenLabs API | $22-99/mo | Commercial |
| Voice (fallback) | Coqui TTS XTTS v2 | Free | MPL 2.0 |
| Terrain Heightmaps | SRTM data + Gaea | Free | Public domain |
| Narrative / Dialogue | Claude API | Per-token | Commercial |
| Radio FX | ffmpeg | Free | LGPL |
| Sprite Assembly | Pillow (Python) | Free | MIT |
| Pipeline Orchestration | Python | Free | PSF |

**Total estimated cost: $50-150/month** for AI services.
Every paid tool has a free/self-hosted fallback.

---

## 9. Legal / Licensing

- **GPL-3.0 applies to code, not assets.** Game assets are "mere aggregation"
  under GPL Section 5 — they don't need to be GPL-licensed.
- **AI-generated assets:** US Copyright Office says purely AI works are not
  copyrightable. Human curation/arrangement adds copyrightable authorship.
  For an open-source project, this is a non-issue.
- **AIVA Pro** explicitly transfers copyright — cleanest legal position for music.
- **milsymbol** is MIT — no restrictions.
- **ITAR:** Does not apply. We use open-source specifications for equipment,
  not classified performance data. A strategy game with generic military units
  is not regulated.
- **AI service ToS:** Use self-hosted Stable Diffusion for any content where
  provider content policies might restrict military themes. ElevenLabs has
  no specific restriction on military speech content.

---

## 10. Phase Integration

| Phase | Assets Generated |
|-------|------------------|
| Phase 0 (current) | Pipeline scaffolding, fallback placeholder assets |
| Phase 1 | NATO symbols for all unit types, basic terrain tiles |
| Phase 2 | Movement effect sprites, formation indicators |
| Phase 3 | Weapon SFX, explosion effects, combat voice lines |
| Phase 4 | ISR overlay graphics, detection level indicators |
| Phase 5 | Logistics icons, supply route graphics |
| Phase 6 | C2 panel UI art, role-specific interface elements |
| Phase 7 | Artillery effect sprites, fire mission SFX |
| Phase 8 | OPFOR voice lines (accented), AI decision narration |
| Phase 9 | Full music suite, scenario briefing illustrations, AAR graphics |
| Phase 10 | Real-world terrain imports, polished UI, tutorial voiceover |
