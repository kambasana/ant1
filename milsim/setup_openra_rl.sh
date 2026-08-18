#!/usr/bin/env bash
# Setup script for OpenRA-RL integration with the MilSim platform.
#
# Prerequisites: Python 3.10+, .NET 6+ SDK, git
#
# Usage:
#   chmod +x milsim/setup_openra_rl.sh
#   ./milsim/setup_openra_rl.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OPENRA_RL_DIR="$REPO_ROOT/openra-rl"

echo "=== OpenRA-RL Setup for MilSim ==="

# 1. Clone OpenRA-RL
if [ ! -d "$OPENRA_RL_DIR" ]; then
    echo "Cloning OpenRA-RL..."
    git clone --recurse-submodules https://github.com/yxc20089/OpenRA-RL.git "$OPENRA_RL_DIR"
else
    echo "OpenRA-RL already cloned at $OPENRA_RL_DIR"
fi

cd "$OPENRA_RL_DIR"

# 2. Apply build fix: resolve CS0121 ambiguity in Map.cs
MAP_CS="OpenRA/OpenRA.Game/Map/Map.cs"
if grep -q 'CryptoUtil.SHA1Hash(\[\])' "$MAP_CS" 2>/dev/null; then
    echo "Applying Map.cs build fix (CS0121 SHA1Hash ambiguity)..."
    sed -i 's/return CryptoUtil\.SHA1Hash(\[\]);/return CryptoUtil.SHA1Hash(Array.Empty<byte>());/' "$MAP_CS"
fi

# 3. Install Python package
echo "Installing openra-rl Python package..."
pip install -e ".[dev]" --ignore-installed PyJWT 2>/dev/null || pip install -e "."

# 4. Build OpenRA .NET engine
echo "Building OpenRA engine..."
cd OpenRA
dotnet build OpenRA.sln
cd ..

# 5. Configure paths
echo "Configuring openra_path..."
ESCAPED_PATH=$(echo "$OPENRA_RL_DIR/OpenRA" | sed 's/\//\\\//g')
sed -i "s|openra_path:.*|openra_path: \"$OPENRA_RL_DIR/OpenRA\"|" config.yaml

echo ""
echo "=== Setup Complete ==="
echo ""
echo "To start the server:"
echo "  cd $OPENRA_RL_DIR"
echo "  OPENRA_PATH=$OPENRA_RL_DIR/OpenRA python -m openra_env.server.app --port 8000"
echo ""
echo "To run foundation tests:"
echo "  python milsim/tests/test_milsim_basics.py"
