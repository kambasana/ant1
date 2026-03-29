# Military Simulation Wargaming Platform

## Project Codename: REDALERT-MILSIM

A turn-based military simulation wargaming platform built on the OpenRA engine (C#/.NET), transforming the Command & Conquer Red Alert RTS foundation into a serious wargaming tool for military professionals and wargamers.

---

## 1. Foundation: Why OpenRA

The [OpenRA](https://github.com/OpenRA/OpenRA) engine is the best starting point because:

- **C#/.NET** — Modern, maintainable, matches our stack
- **Modular architecture** — Game logic separated from engine via YAML-defined mods
- **Existing systems** — Map engine, unit management, pathfinding, fog of war, AI, multiplayer networking all working
- **Active community** — Well-documented, 14k+ stars, regular releases
- **GPL licensed** — Free to fork and modify

Other references:
- [Vanilla Conquer](https://github.com/TheAssemblyArmada/Vanilla-Conquer) — Clean C++ port of original engine (reference for original game mechanics)
- [CnCNet](https://github.com/CnCNet) — Online multiplayer infrastructure (reference for networking)
- [EA CnC_Red_Alert](https://github.com/electronicarts/CnC_Red_Alert) — Original source release (historical reference)

---

## 2. Core Transformation: RTS → Military Simulation

### What We Keep From OpenRA (and modify)

| OpenRA System | Keep/Replace | Military Sim Equivalent |
|---|---|---|
| Tile-based map engine | **Keep & extend** | Hex grid with real terrain data import |
| Unit trait system | **Keep & extend** | Military unit modeling (ORBAT) |
| A* pathfinding | **Keep & extend** | Road networks, convoy movement, terrain cost |
| Fog of war | **Replace** | Graduated ISR (Intelligence, Surveillance, Recon) |
| Damage/armor tables | **Replace** | Probabilistic engagement model |
| AI (scripted bots) | **Replace** | Doctrine-driven OPFOR AI |
| Peer-to-peer networking | **Replace** | Client-server with role-based access |
| Real-time game loop | **Replace** | Turn-based / WEGO (plan then execute) |
| Resource harvesting | **Replace** | Logistics & supply chain system |
| Base building | **Replace** | FOB establishment, supply depots |
| Sidebar UI | **Replace** | C2 (Command & Control) interface |
| Mission/campaign system | **Keep & extend** | Scenario editor with MSEL |

### What We Build New

#### 2.1 Turn-Based / WEGO System
- **Planning phase**: Both sides issue orders simultaneously
- **Execution phase**: Orders resolve over simulated time (e.g. 5-minute ticks)
- **Pause & review**: Players can review results before next planning phase
- **Variable time scale**: Adjustable tick duration (1 min to 30 min per turn)

#### 2.2 Scalable Force Structure
User-selectable operational scale:

| Scale | Player Controls | Units Represent | Map Size |
|---|---|---|---|
| **Platoon** | Company Commander | Individual squads, vehicles | 2km x 2km |
| **Company** | Battalion Commander | Platoons | 10km x 10km |
| **Brigade** | Brigade Commander | Companies/Battalions | 50km x 50km |

Unit hierarchy: Squad → Platoon → Company → Battalion → Brigade
Each unit tracks: Personnel, equipment, ammo, fuel, morale, fatigue, training level.

#### 2.3 Realistic Combat Model
- **Engagement ranges**: Min/max effective range per weapon system
- **Probability of hit/kill (Pk/Ph)**: Based on range, movement, concealment, training
- **Suppression**: Fire suppresses and pins — not just kills
- **Morale & cohesion**: Units can become combat ineffective, retreat, or surrender
- **Combined arms bonus**: Infantry + armor + artillery synergies
- **Attrition**: Gradual degradation, not binary alive/dead

#### 2.4 ISR Model (Replaces Fog of War)
- **Sensor types**: Visual, thermal, radar, SIGINT, HUMINT, UAV
- **Detection probability**: Function of range, terrain, camouflage, movement speed
- **Contact classification**: Unknown → Possible → Probable → Confirmed
- **Intel decay**: Contacts go stale if not re-observed
- **Electronic warfare**: Jamming degrades sensors and comms

#### 2.5 Logistics System (Replaces Resource Harvesting)
- **Supply classes**: Ammo (Class V), Fuel (Class III), Food (Class I), Medical (Class VIII)
- **Supply chain**: Rear depot → MSR/ASR → Forward LOGPACs → Units
- **Consumption rates**: Per unit per turn based on activity (moving, fighting, idle)
- **Interdiction**: Enemy can attack supply routes
- **Resupply missions**: Player must plan and protect logistics convoys

#### 2.6 Command & Control (C2)
- **Communications network**: Radio nets with range limits
- **Orders delay**: Orders take time to propagate down chain of command
- **Commander roles (multiplayer)**: CO, XO, S2 (Intel), S3 (Ops), S4 (Logistics), FSO (Fire Support)
- **OPORD format**: Operations Orders with phases, objectives, phase lines, boundaries
- **Disruption**: Destroy HQ/comms = delayed/lost orders

#### 2.7 Fire Support System
- **Indirect fire**: Mortars, howitzers, MLRS with call-for-fire workflow
- **Fire missions**: Adjust, FFE, suppression, smoke, illumination
- **Close Air Support (CAS)**: Request → approval → time on station → ordnance delivery
- **Counter-battery**: Radar detection of firing positions
- **Naval gunfire**: If coastal scenario

#### 2.8 Terrain & Environment
- **Hex grid**: Standard wargame hexes with terrain types
- **Real-world import**: DTED elevation data, satellite imagery overlays
- **Terrain effects**: Cover, concealment, movement cost, LOS blocking
- **Weather**: Visibility, movement penalties, air operations restrictions
- **Day/night**: Affects detection ranges, NVG advantage
- **Urban terrain (MOUT)**: Special close-combat rules, civilian considerations

#### 2.9 After Action Review (AAR)
- **Full replay**: Step through entire battle with full information
- **Heat maps**: Movement, casualties, fire density
- **Decision timeline**: Annotatable key moments
- **Statistics**: Casualties, ammo expenditure, unit effectiveness
- **Export**: PDF/HTML reports for training debriefs

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      CLIENT (Browser)                           │
│  ┌────────────┐  ┌────────────┐  ┌─────────────┐  ┌─────────┐ │
│  │ Map View   │  │ C2 / OPORD │  │ Unit Detail  │  │ AAR     │ │
│  │ (Canvas/   │  │ Panel      │  │ Panel        │  │ Replay  │ │
│  │  WebGL)    │  │            │  │              │  │ Viewer  │ │
│  └────────────┘  └────────────┘  └─────────────┘  └─────────┘ │
│  Blazor Server + SignalR + HTML5 Canvas (2D) / Three.js (3D)   │
└───────────────────────────┬─────────────────────────────────────┘
                            │ SignalR WebSocket
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                      GAME SERVER (.NET 8)                       │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │              Turn Manager / Game Loop                    │    │
│  │  Planning Phase → Order Validation → Execution Phase    │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────┐     │
│  │ Movement &   │  │ Combat       │  │ ISR / Intel       │     │
│  │ Pathfinding  │  │ Resolution   │  │ Manager           │     │
│  │ Engine       │  │ Engine       │  │                   │     │
│  └──────────────┘  └──────────────┘  └───────────────────┘     │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────┐     │
│  │ Logistics &  │  │ C2 / Comms   │  │ AI / OPFOR        │     │
│  │ Supply       │  │ Network      │  │ Controller        │     │
│  │ Engine       │  │              │  │ (Doctrine-driven) │     │
│  └──────────────┘  └──────────────┘  └───────────────────┘     │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────┐     │
│  │ Fire Support │  │ Terrain &    │  │ AAR Recording     │     │
│  │ Manager      │  │ Environment  │  │ Engine            │     │
│  └──────────────┘  └──────────────┘  └───────────────────┘     │
│                                                                 │
│  Core engine derived from OpenRA (C#/.NET)                      │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                      DATA LAYER                                 │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────┐     │
│  │ Unit/Weapon  │  │ Scenario     │  │ Map / Terrain     │     │
│  │ Database     │  │ Database     │  │ Data Store        │     │
│  │ (real specs) │  │ (missions,   │  │ (hex tiles,       │     │
│  │              │  │  OPORDs)     │  │  elevation)       │     │
│  └──────────────┘  └──────────────┘  └───────────────────┘     │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────┐     │
│  │ Game State   │  │ AAR Records  │  │ User / Session    │     │
│  │ (save/load)  │  │ (replay log) │  │ Management        │     │
│  └──────────────┘  └──────────────┘  └───────────────────┘     │
│                                                                 │
│  PostgreSQL + YAML configs (OpenRA-style) + JSON for scenarios  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 4. Unit Database — Real Military Equipment

### Example: NATO Blue Force

| Category | Examples | Key Stats Modeled |
|---|---|---|
| **Infantry** | Rifle Squad, Weapons Squad, Sniper Team, ATGM Team | Personnel, weapons, AT capability, concealment |
| **Armor** | M1A2 Abrams, Leopard 2A7, Challenger 2 | Armor thickness, main gun Pk, speed, fuel consumption |
| **IFV/APC** | M2 Bradley, Stryker, Warrior, CV90 | Troop capacity, autocannon, AT missiles |
| **Artillery** | M109 Paladin, M777, HIMARS, M120 Mortar | Range, rate of fire, ammo types, displacement time |
| **Air** | AH-64 Apache, A-10, F-16 CAS | Loiter time, ordnance loadout, vulnerability to AD |
| **Air Defense** | Patriot, Avenger, Stinger, SHORAD | Engagement envelope, reload time, radar detection |
| **Recon** | Scout Platoon, Shadow UAV, LRAS3 | Sensor range, stealth, speed |
| **Logistics** | HEMTT, PLS, FARP, Supply Point | Cargo capacity, load/unload time |
| **Engineer** | Route clearance, breaching, obstacle | Breach speed, obstacle types, mine clearing |

### Example: OPFOR Red Force

| Category | Examples |
|---|---|
| **Infantry** | Motor Rifle Squad, Spetsnaz, ATGM Team |
| **Armor** | T-90M, T-72B3, BMP-3 |
| **Artillery** | 2S19 Msta, BM-21 Grad, 2S35 Koalitsiya |
| **Air Defense** | S-300, Pantsir-S1, Tor-M2 |
| **Aviation** | Ka-52, Mi-28N, Su-25 |

---

## 5. Development Phases

### Phase 0: Project Scaffolding (Week 1-2)
- [ ] Fork OpenRA engine into this repository
- [ ] Strip Red Alert game-specific mod content
- [ ] Set up .NET 8 solution structure
- [ ] Create MilSim mod skeleton (YAML-based, OpenRA convention)
- [ ] Basic build pipeline (CI/CD)
- [ ] Verify engine compiles and runs with empty mod

### Phase 1: Hex Map & Unit Rendering (Week 3-5)
- [ ] Replace square tiles with hex grid system
- [ ] Implement terrain types: Open, Forest, Urban, Water, Mountain, Road
- [ ] Create military unit sprites (NATO symbology — APP-6D standard)
- [ ] Unit placement on map
- [ ] Basic camera controls (pan, zoom)
- [ ] Minimap with unit positions

### Phase 2: Turn-Based System & Movement (Week 6-8)
- [ ] Implement WEGO turn manager (Plan → Execute → Review)
- [ ] Movement order system (waypoints on hex grid)
- [ ] Terrain movement costs per unit type
- [ ] Road movement bonus
- [ ] Formation movement (column, line, wedge)
- [ ] Movement animation during execution phase

### Phase 3: Combat Resolution Engine (Week 9-12)
- [ ] Weapon database with real specifications
- [ ] Probability of hit calculation (range, terrain, posture, training)
- [ ] Probability of kill calculation (weapon vs armor)
- [ ] Suppression mechanics (volume of fire → suppression level)
- [ ] Morale system (cohesion → combat effectiveness)
- [ ] Line of sight / line of fire calculation
- [ ] Combat results displayed per unit (casualties, ammo expended)

### Phase 4: ISR & Intelligence (Week 13-15)
- [ ] Replace fog of war with sensor model
- [ ] Visual detection (range based on terrain, movement, size)
- [ ] Contact classification (Unknown → Possible → Probable → Confirmed)
- [ ] Intel decay over time
- [ ] Reconnaissance units with enhanced sensors
- [ ] UAV patrol paths
- [ ] Player-specific intel picture (each side sees different info)

### Phase 5: Logistics & Supply (Week 16-18)
- [ ] Supply class tracking per unit (ammo, fuel, food)
- [ ] Consumption rates based on activity
- [ ] Supply depot and LOGPACs
- [ ] Resupply convoy movement along MSR/ASR
- [ ] Supply route interdiction
- [ ] Low supply effects (reduced combat effectiveness, immobilization)

### Phase 6: Fire Support (Week 19-21)
- [ ] Indirect fire system (mortars, artillery, MLRS)
- [ ] Call-for-fire workflow (request → approve → fire → adjust)
- [ ] Fire mission types (point, area, linear, suppression, smoke)
- [ ] Time of flight and impact delay
- [ ] Counter-battery radar detection
- [ ] CAS request and delivery system
- [ ] Airspace deconfliction

### Phase 7: C2 & Multiplayer (Week 22-25)
- [ ] SignalR-based multiplayer lobby
- [ ] Role-based access (CO, S2, S3, S4, FSO)
- [ ] Communications network simulation
- [ ] Orders delay based on C2 network health
- [ ] OPORD creation interface
- [ ] SITREP / SPOTREP generation
- [ ] Chat / radio log

### Phase 8: AI OPFOR (Week 26-29)
- [ ] Doctrine templates (attack, defend, delay, withdraw)
- [ ] AI decision tree based on correlation of forces
- [ ] Tactical patterns (envelopment, frontal assault, ambush)
- [ ] Reactive AI (responds to player actions)
- [ ] Configurable difficulty / competence levels
- [ ] AI uses same logistics and C2 constraints as players

### Phase 9: Scenario Editor & AAR (Week 30-33)
- [ ] Scenario creation tool (place units, set objectives, define victory conditions)
- [ ] MSEL (Master Scenario Events List) — timed events / injects
- [ ] Save/load game state
- [ ] AAR replay with full information
- [ ] Statistics dashboard (casualties, ammo, movement)
- [ ] Exportable AAR reports (PDF/HTML)

### Phase 10: Polish & Real-World Data (Week 34-36)
- [ ] Import real terrain data (DTED / GeoTIFF)
- [ ] Weather system implementation
- [ ] Day/night cycle
- [ ] Complete unit database with verified specifications
- [ ] Tutorial scenarios
- [ ] Documentation and user guide

### Future: 3D Upgrade Path
- [ ] Three.js / WebGL renderer as alternative to 2D canvas
- [ ] 3D terrain from heightmap data
- [ ] 3D unit models (low-poly military vehicles)
- [ ] Camera: isometric → free-look 3D
- [ ] Maintain same game logic — rendering is swappable

---

## 6. Technical Decisions

| Decision | Choice | Rationale |
|---|---|---|
| **Base engine** | OpenRA (C#/.NET) | Modular, C#, active community, proven RTS systems |
| **Server framework** | ASP.NET Core 8 + SignalR | Real-time multiplayer, role-based auth |
| **Client rendering** | HTML5 Canvas (2D), upgradeable to WebGL/Three.js (3D) | Browser-based, no install needed |
| **Turn system** | WEGO (simultaneous planning, sequential execution) | Standard for professional wargames |
| **Map grid** | Hexagonal | Standard for military wargames, better movement than squares |
| **Unit symbology** | NATO APP-6D | Industry standard, recognizable by military users |
| **Data format** | YAML (unit/weapon defs) + JSON (scenarios) + PostgreSQL (persistent state) | OpenRA convention + flexibility |
| **Combat model** | Probabilistic (Pk/Ph tables) | Realistic, used in real military simulations |
| **Deployment** | Docker + cloud hosting | Easy setup for distributed play |

---

## 7. Directory Structure (Proposed)

```
/milsim/
├── src/
│   ├── MilSim.Engine/              # Core game engine (forked from OpenRA)
│   │   ├── Map/                    # Hex grid, terrain, LOS
│   │   ├── Units/                  # Unit traits, ORBAT hierarchy
│   │   ├── Combat/                 # Engagement resolution, suppression, morale
│   │   ├── Movement/               # Pathfinding, formations, terrain cost
│   │   ├── Intel/                  # ISR sensors, contact tracking, fog
│   │   ├── Logistics/              # Supply chain, consumption, resupply
│   │   ├── FireSupport/            # Indirect fire, CAS, counter-battery
│   │   ├── C2/                     # Comms network, orders, OPORD
│   │   ├── AI/                     # Doctrine-driven OPFOR controller
│   │   ├── TurnManager/            # WEGO turn system
│   │   └── AAR/                    # Recording, replay, statistics
│   │
│   ├── MilSim.Server/              # ASP.NET Core 8 game server
│   │   ├── Hubs/                   # SignalR hubs (game, chat, C2)
│   │   ├── Controllers/            # REST API (scenarios, users, admin)
│   │   ├── Services/               # Game session management
│   │   └── Auth/                   # Role-based authentication
│   │
│   ├── MilSim.Client/              # Blazor client application
│   │   ├── Components/
│   │   │   ├── Map/                # Hex map renderer (Canvas)
│   │   │   ├── C2Panel/            # OPORD, SITREP, orders interface
│   │   │   ├── UnitDetail/         # Selected unit information
│   │   │   ├── FireSupport/        # CFF interface
│   │   │   ├── Intel/              # Intelligence overlay
│   │   │   └── AAR/                # After-action review viewer
│   │   ├── Pages/                  # Lobby, Game, ScenarioEditor, AAR
│   │   └── wwwroot/                # Static assets, sprites, maps
│   │
│   └── MilSim.Data/                # Shared data models and configs
│       ├── Units/                  # Unit definitions (YAML)
│       ├── Weapons/                # Weapon specifications (YAML)
│       ├── Terrain/                # Terrain type definitions
│       ├── Scenarios/              # Pre-built scenarios (JSON)
│       └── Doctrine/               # AI doctrine templates
│
├── tests/
│   ├── MilSim.Engine.Tests/        # Combat model, pathfinding, ISR tests
│   ├── MilSim.Server.Tests/        # API and session tests
│   └── MilSim.Integration.Tests/   # End-to-end scenario tests
│
├── docs/
│   ├── design/                     # Architecture decision records
│   ├── user-guide/                 # Player documentation
│   └── military-reference/         # Doctrine and equipment references
│
├── tools/
│   ├── scenario-editor/            # Standalone scenario creation tool
│   └── data-import/                # DTED/GIS terrain import utilities
│
├── docker-compose.yml
├── MilSim.sln
└── README.md
```

---

## 8. Target Users

### Military Professionals
- Battalion/Brigade staff officers practicing decision-making
- Military academy cadets learning combined arms operations
- War college students studying operational art
- Unit training events (CPX — Command Post Exercises)

### Wargamers
- Tactical wargame enthusiasts (hex-and-counter tradition, digitized)
- Online multiplayer communities
- Scenario designers and modders
- Military history enthusiasts recreating historical battles

---

## 9. Getting Started — Immediate Next Steps

1. **Fork OpenRA** into this repository as a git subtree or submodule
2. **Create the MilSim mod skeleton** following OpenRA's mod structure
3. **Implement hex grid** as the first visible change from base OpenRA
4. **Define initial unit YAML** for a basic scenario (1x platoon vs 1x platoon)
5. **Build the turn manager** to replace real-time with WEGO
6. **Create first playable prototype**: 2 platoons on a small hex map, basic movement + combat
