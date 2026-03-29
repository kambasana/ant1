# LLM Integration Layer — Design Document

## Overview

Integrate a Large Language Model (Claude API) as a dynamic content generation engine
across the military simulation. The LLM acts as a "Narrative AI" that generates
realistic scenarios, drives OPFOR decision-making, produces authentic military
communications, and creates adaptive training experiences that no two playthroughs
are the same.

---

## 1. LLM-Powered Systems

### 1.1 Dynamic Scenario Generation

Instead of hand-crafting every scenario, the LLM generates complete scenarios from
a high-level prompt:

**Input (user provides):**
```
Generate a company-level hasty attack scenario.
Terrain: Eastern European mixed forest and farmland.
BLUFOR: US Mechanized Infantry Company
OPFOR: Russian Motor Rifle Platoon (+) in prepared defense
Training objective: Synchronize direct and indirect fire during assault
Difficulty: Intermediate
```

**LLM generates:**
- Full OPORD (Operations Order) with 5-paragraph format
- Enemy SITTEMP (Situational Template) with most likely and most dangerous COA
- MSEL with 15-20 timed injects
- Terrain description and key terrain analysis
- Weather and light data
- Victory conditions and scoring rubric
- Instructor notes and teaching points

**Output format:** Structured YAML that the engine directly consumes.

### 1.2 Intelligent OPFOR Commander

The LLM acts as the opposing force commander, making decisions based on
military doctrine rather than scripted behavior trees:

```
System prompt: You are a Russian Motor Rifle Battalion commander trained
at the Frunze Military Academy. You follow Russian combined arms doctrine.
You have the following forces: [unit list]. Your mission is to defend
OBJ VOLGA. You are receiving the following intelligence about enemy
activity: [current ISR picture]. What orders do you issue this turn?

Respond in structured format:
- Movement orders (unit, destination, route, formation)
- Fire orders (unit, target, mission type)
- Logistics actions
- Your assessment of enemy intent
- Your decision rationale
```

**Key behaviors:**
- Adapts to player actions (not pre-scripted)
- Makes mistakes appropriate to training level (conscript AI vs elite AI)
- Follows real doctrine (Russian, Chinese, Iranian, insurgent, etc.)
- Explains reasoning in AAR (why it made each decision)

### 1.3 Dynamic MSEL (Master Scenario Events List)

The LLM generates and adapts injects in real-time based on game state:

**Static MSEL:** Pre-planned events (reinforcements at turn 4, weather change at turn 8)

**Dynamic MSEL:** LLM evaluates game state each turn and generates contextual injects:
- "SIGINT intercept indicates OPFOR battalion commander requesting air support"
- "Civilian vehicle convoy approaching checkpoint on MSR TAMPA"
- "Report from adjacent unit: enemy reconnaissance element spotted at grid 45678901"
- "Higher HQ message: ROE change — minimize collateral damage in built-up area"
- "Medical evacuation request from 2nd Platoon — 3 urgent surgical patients"

**Escalation logic:** If the player is doing too well, LLM introduces realistic
complications. If struggling, it may inject enabling events (flanking unit reports
in, additional fire support allocated).

### 1.4 Realistic Military Communications

The LLM generates authentic radio traffic and reports:

**SITREP (Situation Report):**
```
"BAYONET 6, this is BAYONET 2-6. SITREP follows. We are at grid 4567 8901.
Engaged dismounted enemy platoon vicinity OBJ BRAVO at 0345Z. Estimate enemy
squad-size element in prepared fighting positions oriented south. We have
2 x WIA, 1 x urgent surgical. Request MEDEVAC at LZ ROBIN, grid 4568 8902.
Currently consolidating on OBJ BRAVO. Ammo status amber. Over."
```

**OPORD Briefings:** Full 5-paragraph operations orders with overlays
**FRAGO:** Fragmentary orders when the plan changes
**Intel Summaries:** S2 intelligence summaries with enemy assessment
**Battle Damage Assessment:** Post-engagement reports

All generated with proper military formatting, brevity codes, and radio procedure.

### 1.5 After Action Review Narratives

Post-game, the LLM analyzes the replay data and generates:

- **Battle narrative:** "At 0600Z, 1st Platoon initiated the assault on OBJ ALPHA
  from the east. The supporting effort by 2nd Platoon was delayed by 2 turns due
  to an undetected minefield on Route BLUE, which caused the main effort to be
  unsupported during the critical breach phase..."
- **Decision analysis:** "The commander's decision to commit the reserve at Turn 8
  was premature — the OPFOR had not yet committed their counterattack force.
  A better option would have been to..."
- **Lessons learned:** Extracted from doctrine, tailored to what happened
- **Scoring rationale:** Why each objective was scored the way it was

### 1.6 Adaptive Training Difficulty

The LLM monitors player performance across sessions and adjusts:

- **Skill assessment:** Tracks which tactical skills the player demonstrates
- **Weakness identification:** "Player consistently neglects flank security"
- **Scenario tailoring:** Next generated scenario specifically tests weak areas
- **Progressive complexity:** Gradually introduces new challenges (night ops, urban,
  chemical environment, coalition forces)

### 1.7 Civilian Population & ROE

LLM generates realistic civilian interactions:

- Civilian movement patterns on roads and in towns
- IED/ambush decisions for insurgent scenarios
- ROE (Rules of Engagement) dilemmas requiring judgment calls
- Cultural interactions (interpreters, local leaders, NGOs)
- Collateral damage assessment and reporting
- Media presence and information operations

---

## 2. Technical Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    GAME ENGINE                               │
│                                                             │
│  Game State ──→ LLM Context Builder ──→ Prompt Assembly     │
│                                              │              │
│                                              ▼              │
│                                     ┌─────────────────┐    │
│                                     │  Claude API      │    │
│                                     │  (Anthropic SDK) │    │
│                                     └────────┬────────┘    │
│                                              │              │
│                                              ▼              │
│                                     Response Parser         │
│                                         │                   │
│                         ┌───────────────┼───────────────┐   │
│                         ▼               ▼               ▼   │
│                   OPFOR Orders    MSEL Injects    Comms/    │
│                   (YAML)         (YAML)          Reports   │
│                         │               │          (Text)   │
│                         ▼               ▼               ▼   │
│                    Order Queue    Event Queue    UI Panel   │
└─────────────────────────────────────────────────────────────┘
```

### 2.1 Context Builder

Each LLM call includes structured context:

```csharp
public class MilSimLLMContext
{
    // Scenario metadata
    public string ScenarioName { get; set; }
    public string Scale { get; set; }         // Platoon/Company/Brigade
    public int CurrentTurn { get; set; }
    public string TimeOfDay { get; set; }
    public string Weather { get; set; }

    // Force status (what the AI-controlled side knows)
    public List<UnitStatus> OwnForces { get; set; }
    public List<IntelContact> KnownEnemyContacts { get; set; }
    public string CurrentMission { get; set; }
    public string CommanderIntent { get; set; }

    // Recent events
    public List<GameEvent> RecentEvents { get; set; }  // Last 3 turns
    public List<string> RecentComms { get; set; }

    // Doctrine reference
    public string DoctrineName { get; set; }  // "Russian Combined Arms", etc.
    public string DoctrineNotes { get; set; }  // Key principles

    // Player performance (for adaptive difficulty)
    public PlayerProfile PlayerProfile { get; set; }
}
```

### 2.2 Prompt Templates

Stored as configurable templates:

```
prompts/
├── opfor_commander.txt       # OPFOR decision-making
├── scenario_generator.txt    # Full scenario generation
├── msel_inject.txt          # Dynamic event injection
├── sitrep_generator.txt     # Situation report formatting
├── opord_generator.txt      # Operations order generation
├── aar_narrative.txt        # After action review narrative
├── aar_analysis.txt         # Decision point analysis
├── civilian_behavior.txt    # Civilian population actions
├── roe_dilemma.txt          # Rules of engagement scenarios
├── radio_traffic.txt        # Authentic radio communications
└── difficulty_adapter.txt   # Training difficulty assessment
```

### 2.3 Response Parsing

LLM responses are structured with clear delimiters for reliable parsing:

```
<orders>
- unit: 2nd_motor_rifle_platoon
  action: move
  destination: [45, 32]
  route: covered
  formation: column
  priority: immediate
</orders>

<fire_missions>
- unit: mortar_battery
  target: [43, 30]
  mission_type: suppression
  rounds: 12
  duration: 2_minutes
</fire_missions>

<rationale>
Enemy main effort appears to be approaching from the southwest along
Route BLUE. I am repositioning 2nd Platoon to reinforce the left flank
while maintaining 1st Platoon in prepared positions on OBJ VOLGA.
Mortar suppression will slow their advance through the open ground
at grid 4330.
</rationale>
```

### 2.4 Caching & Cost Management

- **Prompt caching:** Use Claude's prompt caching for doctrine references and
  scenario context that doesn't change between turns
- **Batch processing:** Collect all LLM needs per turn into a single batch call
- **Tiered usage:**
  - Scenario generation: Full Claude (Opus/Sonnet) for quality
  - OPFOR decisions: Claude Sonnet for speed + quality balance
  - Radio chatter: Claude Haiku for high volume, low cost
  - AAR narratives: Claude Opus for deep analysis
- **Offline fallback:** Pre-generated response pools for when API unavailable
- **Token budget:** Configurable per-turn token budget to control costs

### 2.5 API Integration

Using the Anthropic C# SDK:

```csharp
public class MilSimAIService
{
    private readonly AnthropicClient _client;
    private readonly PromptTemplateService _templates;
    private readonly ResponseParserService _parser;

    public async Task<OPFOROrders> GetOPFORDecision(
        MilSimLLMContext context, CancellationToken ct)
    {
        var prompt = _templates.Build("opfor_commander", context);

        var response = await _client.Messages.CreateAsync(new()
        {
            Model = "claude-sonnet-4-6",
            MaxTokens = 2048,
            System = prompt.SystemPrompt,
            Messages = prompt.Messages,
        }, ct);

        return _parser.ParseOPFOROrders(response.Content);
    }

    public async Task<Scenario> GenerateScenario(
        ScenarioRequest request, CancellationToken ct)
    {
        var prompt = _templates.Build("scenario_generator", request);

        var response = await _client.Messages.CreateAsync(new()
        {
            Model = "claude-opus-4-6",
            MaxTokens = 8192,
            System = prompt.SystemPrompt,
            Messages = prompt.Messages,
        }, ct);

        return _parser.ParseScenario(response.Content);
    }
}
```

---

## 3. LLM-Enhanced Features by Phase

| Phase | LLM Feature | Model Tier |
|-------|-------------|------------|
| Phase 3 (Combat) | Combat narration — describe engagements in prose | Haiku |
| Phase 4 (ISR) | Intel report generation, SIGINT intercept text | Haiku |
| Phase 7 (Fire Support) | Call-for-fire radio dialogue generation | Haiku |
| Phase 8 (AI OPFOR) | **Full OPFOR commander AI** | Sonnet |
| Phase 9 (Scenarios) | **Dynamic scenario generation from prompts** | Opus |
| Phase 9 (AAR) | **Narrative AAR with decision analysis** | Opus |
| Phase 10 (Polish) | Adaptive training, civilian ROE dilemmas | Sonnet |

---

## 4. Example: Full Turn with LLM

```
TURN 6 — Execution Phase

1. Player submitted orders during planning phase
2. Engine requests OPFOR orders from LLM:

   Context: "OPFOR has 2 platoons defending OBJ VOLGA. ISR shows enemy
   company approaching from SW. 1st Platoon reports taking indirect fire.
   Ammo status: GREEN. Morale: STEADY."

   LLM responds with movement, fire, and logistics orders + rationale.

3. Engine requests dynamic MSEL check:

   Context: "Turn 6 of 24. Player is on schedule. No major setbacks.
   Current training objective: fire support synchronization."

   LLM responds: "Inject — Higher HQ reports: 'Chemical contamination
   detected at grid 4560 8890. MOPP level 2 authorized.' This forces
   the player to manage degraded operations while maintaining tempo."

4. Engine resolves all orders simultaneously.

5. Engine requests comms generation for key events:

   Context: "2nd Platoon engaged OPFOR AT team at 300m, destroyed with
   combined MG and grenade fire. 1 x WIA friendly."

   LLM generates: "BAYONET 6, BAYONET 2-6. Contact, AT team, grid 4565
   8899. Engaged and destroyed with direct fire. 1 x WIA, cat B. Continuing
   mission. Over."

6. Results displayed to player with generated radio traffic in comms panel.
```

---

## 5. Doctrine Library (LLM System Prompts)

Pre-built doctrine profiles for OPFOR AI:

```
doctrines/
├── russian_combined_arms.md     # BTG tactics, echeloned defense/attack
├── chinese_pla.md               # PLA doctrine, systems warfare
├── iranian_hybrid.md            # Conventional + asymmetric mix
├── insurgent_guerrilla.md       # IED, ambush, hit-and-run
├── nato_conventional.md         # NATO defensive/offensive doctrine
├── peer_near_peer.md            # Advanced integrated air defense, EW
└── historical/
    ├── ww2_german.md            # Auftragstaktik, combined arms
    ├── ww2_soviet.md            # Deep operations, maskirovka
    └── cold_war_warsaw_pact.md  # Echeloned attack, nuclear preparation
```

Each doctrine file serves as part of the LLM system prompt, giving the AI
commander authentic decision-making patterns, preferred tactics, typical
formations, and known weaknesses.

---

## 6. Privacy & Security Considerations

- **No real classified data** in prompts — all doctrine based on open-source
  publications (FM 7-8, ATP 3-21.8, open-source foreign doctrine analysis)
- **API key management** — stored in environment variables, never in code
- **Data retention** — configure Claude API to not retain training data
- **Offline mode** — pre-generated response pools for air-gapped environments
- **Audit logging** — all LLM prompts and responses logged for review
