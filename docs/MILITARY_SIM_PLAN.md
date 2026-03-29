# Military Simulation Wargaming Platform — Project Plan

## Project Codename: REDALERT-MILSIM

**Based on:** OpenRA engine (C#/.NET, GPL-3.0, 16,500+ stars)
**Target Audience:** Military professionals and wargamers
**Mode:** Turn-based (WEGO — simultaneous planning, then execution)
**Scale:** Configurable — Platoon, Company, or Brigade level
**Rendering:** 2D tile-based (3D upgrade path planned for later)

---

## 1. Why OpenRA as the Base

| Factor | Detail |
|--------|--------|
| **Language** | C#/.NET — modern, maintainable, large talent pool |
| **Architecture** | Component-based entity system ("traits") — units are composed of behaviors |
| **Modding framework** | Games are implemented as mods on the engine — we build our sim as a new mod |
| **Multiplayer** | Built-in internet and LAN multiplayer with spectator support |
| **Map system** | Tile-based terrain with elevation, passability, and built-in editor |
| **Rendering** | OpenGL/SDL2, cross-platform (Windows, Linux, macOS) |
| **Mission scripting** | Lua scripting for scenarios without recompiling |
| **Proven** | "Red Alert - Real War" mod already demonstrates realistic military gameplay |
| **Community** | 2,891 forks, active development through 2026 |

### Other Projects Evaluated

| Project | Repo | Why Not Primary |
|---------|------|-----------------|
| EA CnC_Red_Alert | `electronicarts/CnC_Red_Alert` | Original 1996 C/ASM, doesn't compile, read-only |
| Vanilla Conquer | `TheAssemblyArmada/Vanilla-Conquer` | C++, faithful port but harder to extend |
| CnCNet | `CnCNet/xna-cncnet-client` | Multiplayer wrapper, not an engine |
| Chrono Divide | `chronodivide.com` | TypeScript/browser, engine not open source |

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    CLIENT / UI LAYER                         │
│                                                             │
│  ┌──────────────┐  ┌─────────────┐  ┌───────────────────┐  │
│  │ Tactical Map │  │ C2 / OPORD  │  │ AAR Replay        │  │
│  │ (2D tile     │  │ Panel       │  │ Viewer            │  │
│  │  renderer)   │  │             │  │                   │  │
│  └──────────────┘  └─────────────┘  └───────────────────┘  │
│                                                             │
│  OpenRA renderer (OpenGL/SDL2) + custom UI widgets          │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                    GAME SERVER / ENGINE                      │
│                                                             │
│  ┌──────────────┐  ┌─────────────┐  ┌───────────────────┐  │
│  │ Turn/Phase   │  │ Combat      │  │ AI / OPFOR        │  │
│  │ Manager      │  │ Resolution  │  │ Doctrine Engine   │  │
│  │ (WEGO loop)  │  │ Engine      │  │                   │  │
│  └──────────────┘  └─────────────┘  └───────────────────┘  │
│                                                             │
│  ┌──────────────┐  ┌─────────────┐  ┌───────────────────┐  │
│  │ Movement &   │  │ ISR /       │  │ Logistics &       │  │
│  │ Pathfinding  │  │ Intel Mgr   │  │ Supply Engine     │  │
│  └──────────────┘  └─────────────┘  └───────────────────┘  │
│                                                             │
│  ┌──────────────┐  ┌─────────────┐  ┌───────────────────┐  │
│  │ Artillery &  │  │ C2 / Comms  │  │ Scenario /        │  │
│  │ Fire Support │  │ Network     │  │ MSEL Engine       │  │
│  └──────────────┘  └─────────────┘  └───────────────────┘  │
│                                                             │
│  Core: OpenRA engine (forked) + custom traits/systems       │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                    DATA LAYER                                │
│                                                             │
│  ┌──────────────┐  ┌─────────────┐  ┌───────────────────┐  │
│  │ Unit / ORBAT │  │ Weapon &    │  │ Map / Terrain     │  │
│  │ Database     │  │ Equipment   │  │ Data              │  │
│  │ (YAML)       │  │ Specs (YAML)│  │ (tiles + height)  │  │
│  └──────────────┘  └─────────────┘  └───────────────────┘  │
│                                                             │
│  ┌──────────────┐  ┌─────────────┐  ┌───────────────────┐  │
│  │ Scenario     │  │ Doctrine    │  │ AAR / Replay      │  │
│  │ Definitions  │  │ Templates   │  │ Logs              │  │
│  └──────────────┘  └─────────────┘  └───────────────────┘  │
│                                                             │
│  OpenRA YAML rule system + Lua mission scripts              │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. Core Systems to Build

### 3.1 WEGO Turn System (Replace Real-Time)

OpenRA runs real-time with a lockstep tick. We replace this with WEGO:

1. **Planning Phase** — All players issue orders simultaneously (movement waypoints, fire missions, stance changes). Time-limited or open.
2. **Execution Phase** — Engine resolves all orders simultaneously over N simulated ticks. Players watch but cannot intervene.
3. **Review Phase** — Brief pause to see results before next planning phase.

**Implementation:** New `TurnManager` trait that gates order submission, freezes input during execution, and manages phase transitions. Hook into OpenRA's `OrderManager` and `World.Tick()`.

### 3.2 Realistic Force Structure (Replace C&C Units)

**Scale selector at game start:** Platoon / Company / Brigade

| Scale | Base Unit | Example |
|-------|-----------|---------|
| Platoon | Individual soldier / vehicle | 4x M1A2 Abrams = Tank Platoon |
| Company | Fire team / squad | 3x Rifle Platoons + Weapons Plt = Rifle Company |
| Brigade | Platoon / company | 3x Battalions + Support = Brigade Combat Team |

**Unit data model (YAML):**
```yaml
InfantryRifleSquad:
  DisplayName: Rifle Squad (9-man)
  Category: Infantry
  Echelon: Squad
  Personnel: 9
  Inherits: ^MilSimUnit
  Health:
    HP: 900  # 100 per soldier
  MilSimMovement:
    Speed: 4        # km/h dismounted
    SpeedRoad: 6
    Fatigue: true
    FatigueRate: 5  # per hour of movement
  MilSimCombat:
    Weapons: M4Carbine, M249SAW, M320Grenade
    Range: 300       # meters effective
    RateOfFire: 30   # rounds/min sustained
    Suppression: 40  # suppression output
    SuppressionResist: 20
  MilSimLogistics:
    AmmoCapacity: 2100  # rounds
    AmmoConsumption: 50 # rounds per combat minute
    WaterDays: 2
    FoodDays: 3
  MilSimMorale:
    Morale: 100
    Training: Trained  # Green, Trained, Veteran, Elite
    CohesionBonus: 10
  MilSimDetection:
    VisualSignature: 15
    ThermalSignature: 5
    NoiseSignature: 20
    CamouflageRating: 30
```

### 3.3 Probabilistic Combat Model (Replace Damage Tables)

Replace C&C's simple `Damage * Modifier` with:

```
P(hit) = f(range, weapon_accuracy, target_movement, target_posture,
           terrain_cover, visibility, training_level)

P(kill|hit) = f(weapon_penetration, target_armor, hit_location,
               angle_of_impact)

Suppression = f(volume_of_fire, proximity, target_morale,
                target_cover, experience)
```

**Effects:**
- **Kill** — Target destroyed/KIA
- **Mobility Kill** — Vehicle immobilized, crew alive
- **Firepower Kill** — Weapon system disabled
- **Suppressed** — Unit cannot return effective fire, movement halted
- **Pinned** — Unit frozen, morale dropping
- **Broken** — Unit retreats uncontrollably

### 3.4 ISR / Intelligence System (Replace Fog of War)

OpenRA's fog of war is binary (seen / not seen). Replace with graduated intelligence:

| Level | Label | What You Know |
|-------|-------|---------------|
| 0 | No Contact | Nothing |
| 1 | Suspected | Something may be there (SIGINT intercept, pattern analysis) |
| 2 | Possible | Unconfirmed contact (single sensor, brief detection) |
| 3 | Probable | Multiple sources corroborate |
| 4 | Confirmed | Direct observation, high confidence |

**Sensor types as traits:**
- `VisualSensor` — Mk.1 eyeball, binoculars, scopes (range varies by terrain/weather)
- `ThermalSensor` — FLIR, thermal sights (works at night, degraded by weather)
- `RadarSensor` — Ground surveillance radar (detects movement)
- `SIGINTSensor` — Intercepts radio comms (detects HQ units)
- `AcousticSensor` — Detects vehicles/artillery by sound

**Intel decay:** Contacts degrade over time without continued observation. A "Confirmed" contact becomes "Probable" after N turns without re-observation.

### 3.5 Logistics & Supply (Replace Base Building)

No base building. Instead:

- **Supply Points (SP)** — Abstract resource representing ammo, fuel, food, medical
- **Supply Depots** — Fixed locations that generate/store SP
- **Supply Routes** — MSR (Main Supply Route) and ASR (Alternate Supply Route) defined on map
- **Supply Convoys** — Units that carry SP along routes (can be interdicted)
- **Consumption:** Every unit consumes SP each turn based on activity:
  - Static/defensive: 1 SP/turn
  - Moving: 2 SP/turn
  - Combat: 5 SP/turn
- **Low supply effects:** Reduced movement, reduced fire rate, morale penalty
- **No supply:** Unit combat ineffective after 3 turns, surrenders after 6

### 3.6 Command & Control (C2)

- **Command hierarchy:** Units belong to a chain of command
- **Order delay:** Orders from higher HQ take 1-2 turns to propagate down
- **Comms network:** Radio nets can be jammed or disrupted
- **Commander roles (multiplayer):**
  - **CO** — Overall commander, sets objectives and phase lines
  - **S3 (Operations)** — Issues movement orders, manages scheme of maneuver
  - **S2 (Intelligence)** — Controls ISR assets, manages intel picture
  - **FSO (Fire Support)** — Controls artillery, CAS, fire missions
  - **S4 (Logistics)** — Manages supply routes, depot placement, convoy dispatch

### 3.7 Artillery & Fire Support

**Indirect fire system:**
- `CallForFire` order → delay (1-3 turns based on echelon) → rounds impact
- Fire mission types: Adjust Fire, Fire For Effect, Suppression, Smoke, Illumination
- Counter-battery: Radar detects firing positions, enables return fire
- Close Air Support: Request through chain, aircraft has loiter time and ordnance loadout
- Danger close rules: Friendly fire risk when firing near own troops

### 3.8 Terrain & Environment

**Extend OpenRA's tile system:**
- **Elevation:** Affects line of sight, range bonuses for high ground
- **Cover types:** None, Light (bushes), Medium (walls), Heavy (bunker), Fortified
- **Concealment:** Separate from cover — hides from detection but doesn't stop bullets
- **Urban terrain:** Buildings as enterable structures, room-clearing mechanics
- **Roads:** Movement speed multiplier on roads
- **Rivers/bridges:** Crossing points, bridge demolition
- **Weather:** Clear, Rain (reduced visibility), Fog (severely reduced), Snow (reduced movement)
- **Day/Night:** Affects visual detection range, NVG advantage

### 3.9 After Action Review (AAR)

- **Full replay:** Step through every turn with full information (both sides revealed)
- **Heat maps:** Movement density, engagement locations, casualty locations
- **Timeline:** Key events plotted on timeline (contacts, engagements, objectives)
- **Statistics:** Casualties (KIA/WIA/MIA), ammo expended, objectives achieved, time in phases
- **Annotations:** Players/instructors can annotate decision points
- **Export:** PDF/HTML report for training debriefs

### 3.10 Scenario / MSEL System

**Scenarios defined in YAML + Lua:**
```yaml
Scenario:
  Name: "Hasty Attack - Mechanized Infantry"
  Scale: Company
  Terrain: European Mixed
  Weather: Clear
  TimeOfDay: Dawn
  Duration: 24 turns (each turn = 30 minutes)

  BLUFOR:
    - MechInfantryCompany: full_strength
    - TankPlatoon: attached
    - MortarSection: attached

  OPFOR:
    - MotorizedRiflePlatoon: defending
    - ATGMTeam: concealed
    - MortarSection: in_support

  Objectives:
    - SeizeObjective:
        Name: "OBJ ALPHA"
        Location: [45, 32]
        Points: 100
    - PreserveForce:
        MinPercent: 70
        Points: 50

  MSEL:  # Master Scenario Events List
    - Turn 4: "SIGINT indicates OPFOR reinforcement platoon moving from NE"
    - Turn 8: "Weather deteriorates — visibility drops to 500m"
    - Turn 12: "Civilian convoy on MSR TAMPA — rules of engagement apply"
```

---

## 4. Development Phases

### Phase 0 — Project Scaffolding (Current)
- [x] Create project plan
- [ ] Fork OpenRA engine
- [ ] Set up build system and CI
- [ ] Create `milsim` mod directory structure
- [ ] Strip C&C-specific content, establish clean mod template
- [ ] First successful build with empty mod

### Phase 1 — Map & Unit Rendering
- [ ] Define base unit types in YAML (infantry, armor, artillery, logistics)
- [ ] Create placeholder unit sprites (NATO military symbols)
- [ ] Render units on OpenRA map with correct symbology
- [ ] Implement scale selector (platoon/company/brigade)
- [ ] Basic unit selection and info panel

### Phase 2 — WEGO Turn System & Movement
- [ ] Implement TurnManager (planning → execution → review phases)
- [ ] Waypoint-based movement orders during planning phase
- [ ] Terrain movement cost modifiers (road, cross-country, urban, river)
- [ ] Fatigue system affecting movement speed
- [ ] Formation movement (column, line, wedge)

### Phase 3 — Combat Resolution
- [ ] Probabilistic hit/kill model
- [ ] Weapon range and accuracy curves
- [ ] Cover and concealment modifiers
- [ ] Suppression and morale effects
- [ ] Unit destruction, mobility kills, firepower kills
- [ ] Combined arms synergy bonuses

### Phase 4 — ISR & Intelligence
- [ ] Sensor trait system (visual, thermal, radar, SIGINT)
- [ ] Graduated detection levels (suspected → confirmed)
- [ ] Intel decay over time
- [ ] Fog of war replacement with intel overlay
- [ ] Reconnaissance unit types (scouts, UAVs)

### Phase 5 — Logistics & Supply
- [ ] Supply point resource system
- [ ] Supply depot placement
- [ ] Supply route designation (MSR/ASR)
- [ ] Convoy units and interdiction
- [ ] Consumption model and low-supply effects

### Phase 6 — C2 & Multiplayer Roles
- [ ] Command hierarchy system
- [ ] Order propagation delay
- [ ] Multiplayer role assignment (CO, S2, S3, FSO, S4)
- [ ] Role-specific UI panels
- [ ] Communications jamming mechanic

### Phase 7 — Artillery & Fire Support
- [ ] Call-for-fire order system
- [ ] Fire mission types (FFE, suppression, smoke, illumination)
- [ ] Processing delay based on echelon
- [ ] Counter-battery radar detection
- [ ] Close Air Support requests
- [ ] Danger close / friendly fire risk

### Phase 8 — AI / OPFOR
- [ ] Doctrine-driven decision engine
- [ ] Defensive doctrine (prepared defense, delay, withdraw)
- [ ] Offensive doctrine (hasty attack, deliberate attack, exploitation)
- [ ] AI ISR and intel management
- [ ] AI logistics management
- [ ] Difficulty scaling (conscript → professional → elite)

### Phase 9 — Scenario Editor & AAR
- [ ] YAML scenario definition parser
- [ ] Lua MSEL scripting
- [ ] In-game scenario editor
- [ ] AAR replay system
- [ ] Heat maps and statistics
- [ ] Export to PDF/HTML

### Phase 10 — Polish & Real-World Data
- [ ] Real equipment specifications database
- [ ] NATO/Warsaw Pact ORBAT templates
- [ ] Real-world terrain import (DTED/GIS)
- [ ] Day/night cycle and weather system
- [ ] Tutorial scenarios
- [ ] Documentation and user guide

---

## 5. File Structure (milsim mod)

```
mods/milsim/
├── mod.yaml                    # Mod manifest
├── rules/
│   ├── world.yaml              # World/global rules
│   ├── player.yaml             # Player/faction definitions
│   ├── defaults.yaml           # Base trait templates
│   ├── infantry.yaml           # Infantry unit definitions
│   ├── armor.yaml              # Armored vehicle definitions
│   ├── artillery.yaml          # Artillery unit definitions
│   ├── logistics.yaml          # Supply/transport definitions
│   ├── aircraft.yaml           # Rotary/fixed wing definitions
│   ├── structures.yaml         # Depots, FOBs, bridges
│   └── weapons.yaml            # Weapon system definitions
├── maps/
│   ├── training_ground/        # Tutorial map
│   ├── european_plains/        # Open terrain scenario
│   ├── urban_assault/          # MOUT scenario
│   └── mountain_pass/          # Restricted terrain scenario
├── scenarios/
│   ├── hasty_attack.yaml
│   ├── deliberate_defense.yaml
│   ├── movement_to_contact.yaml
│   └── convoy_escort.yaml
├── scripts/
│   ├── msel_engine.lua         # MSEL event scripting
│   ├── aar_logger.lua          # AAR data collection
│   └── ai_doctrine.lua         # AI behavior scripts
├── sprites/
│   ├── nato_symbols/           # NATO unit symbology
│   ├── terrain/                # Terrain tiles
│   └── effects/                # Explosion, smoke, tracer effects
├── ui/
│   ├── c2_panel.yaml           # Command & Control interface
│   ├── fire_support.yaml       # Fire support request panel
│   ├── intel_overlay.yaml      # ISR display
│   ├── logistics_panel.yaml    # Supply management
│   └── aar_viewer.yaml         # After Action Review
└── audio/
    ├── weapons/                # Weapon sounds
    ├── vehicles/               # Engine sounds
    └── comms/                  # Radio chatter
```

---

## 6. Key Technical Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Base engine | OpenRA (fork) | C#/.NET, modular, multiplayer built-in |
| Game mode | WEGO turn-based | More realistic for military planning, allows multi-role play |
| Scale | Configurable | Serves both tactical and operational audiences |
| Unit data format | YAML | OpenRA native, human-readable, easy to edit |
| Scenario scripting | Lua | OpenRA native, powerful, no recompilation needed |
| Rendering | 2D (OpenGL/SDL2) | Faster to develop, 3D upgrade path via OpenRA's renderer later |
| Unit graphics | NATO symbols | Military standard, no art assets needed to start |
| Multiplayer | OpenRA built-in | Already handles networking, lobbies, sync |
| AI | Doctrine-driven | More realistic than scripted, trainable |
| AAR | Built-in replay + export | Critical for training value |

---

## 7. Immediate Next Steps (Phase 0)

1. **Fork OpenRA** into this repo as a git submodule or direct integration
2. **Build OpenRA** from source — verify it compiles and runs
3. **Create empty `milsim` mod** using OpenRA's mod template
4. **Define base trait interfaces** for MilSim-specific systems:
   - `IMilSimMovement` — fatigue, formation, terrain costs
   - `IMilSimCombat` — probabilistic engagement, suppression
   - `IMilSimLogistics` — supply consumption and state
   - `IMilSimMorale` — morale, cohesion, training level
   - `IMilSimDetection` — multi-sensor ISR
5. **Create first unit definition** — basic rifle squad with all traits
6. **Render on map** — NATO symbol on OpenRA terrain

---

## References

- [OpenRA Engine](https://github.com/OpenRA/OpenRA) — Base engine (C#, GPL-3.0)
- [EA CnC_Red_Alert Source](https://github.com/electronicarts/CnC_Red_Alert) — Original 1996 source
- [Vanilla Conquer](https://github.com/TheAssemblyArmada/Vanilla-Conquer) — Modern C++ port
- [CnCNet](https://github.com/CnCNet) — Online multiplayer platform
- [Chrono Divide](https://chronodivide.com/) — Browser-based RA2 recreation
