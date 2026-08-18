# OpenRA-RL Integration

The MilSim platform builds on [OpenRA-RL](https://github.com/yxc20089/OpenRA-RL) (v0.4.1), a reinforcement learning framework for OpenRA that provides the game engine bridge, observation space, and action interface.

## What OpenRA-RL Provides

| Layer | Capability |
|-------|-----------|
| Game Engine | Modified OpenRA (C#/.NET) with gRPC bridge at 25 ticks/second |
| Observation | 9-channel spatial tensor, unit/building state, economy, military stats, fog of war |
| Actions | 23 action types: move, attack, build, deploy, stance, guard, transport, etc. |
| Python API | Gymnasium-style `reset()`/`step()`/`close()` via WebSocket + FastAPI |
| MCP Tools | 48 tools for LLM agents (observation, knowledge, movement, production, utility) |
| Reward | 6-component scalar + 8-dimension vector (combat, economy, infrastructure, intelligence, composition, tempo, disruption, outcome) |
| Multi-session | Concurrent game instances via gRPC session management |

## Architecture

```
Python FastAPI (port 8000) → gRPC bridge (port 9999) → Modified OpenRA C# Engine
     ↑                                                        ↑
  MCP Tools / WebSocket Client                          Trait System (ECS)
     ↑                                                   Deterministic Lockstep
  LLM Agent / RL Training                               Map / Actors / Orders
```

## What MilSim Adds

| Feature | Status | Description |
|---------|--------|-------------|
| WEGO Turn System | Planned | Planning/execution/review phases injected into tick loop |
| Probabilistic Combat | Planned | Replace deterministic damage with probability distributions |
| ISR Graduation | Planned | 5-level intelligence classification (Detection → Identification) |
| NATO Symbology | Planned | Standard military map symbols |
| Logistics & Morale | Planned | Supply lines, unit morale, fatigue |
| C2 Interface | Planned | Commander decision interface with AAR |
| Doctrine Profiles | Scaffolded | Russian, insurgent, Iranian hybrid |
| Scenarios | Scaffolded | Hasty attack, strait defense, urban clearance, eastern Europe |

## Setup

```bash
./milsim/setup_openra_rl.sh
```

Or manually:

```bash
# Clone with submodules
git clone --recurse-submodules https://github.com/yxc20089/OpenRA-RL.git openra-rl
cd openra-rl

# Fix build (CS0121 SHA1Hash ambiguity in Map.cs)
sed -i 's/CryptoUtil.SHA1Hash(\[\]);/CryptoUtil.SHA1Hash(Array.Empty<byte>());/' \
    OpenRA/OpenRA.Game/Map/Map.cs

# Install Python package
pip install -e ".[dev]" --ignore-installed PyJWT

# Build .NET engine
cd OpenRA && dotnet build OpenRA.sln && cd ..

# Update config.yaml openra_path
```

## Running

```bash
# Start server
cd openra-rl
OPENRA_PATH=$(pwd)/OpenRA python -m openra_env.server.app --port 8000

# Run foundation tests
python milsim/tests/test_milsim_basics.py
```

## Key Files (OpenRA-RL)

| File | Purpose |
|------|---------|
| `openra_env/client.py` | WebSocket client (`OpenRAEnv`) |
| `openra_env/server/app.py` | FastAPI server |
| `openra_env/server/bridge_client.py` | gRPC client to OpenRA |
| `openra_env/models.py` | Pydantic models (21 action types, observations) |
| `openra_env/mcp_server.py` | 48 MCP tools |
| `openra_env/reward.py` | Reward computation |
| `proto/rl_bridge.proto` | gRPC service definition |
| `config.yaml` | Server configuration |
| `examples/scripted_bot.py` | Reference bot implementation |

## Spatial Tensor (9 Channels)

The observation space includes a per-cell tensor ideal for ISR:

| Channel | Content | MilSim Use |
|---------|---------|-----------|
| 0 | Terrain type | Terrain analysis |
| 1 | Height | Elevation / LOS |
| 2 | Resources | Supply identification |
| 3 | Passability | Movement planning |
| 4 | Fog of war | ISR coverage |
| 5-6 | Allied positions | Blue force tracking |
| 7-8 | Enemy positions | Red force tracking |
