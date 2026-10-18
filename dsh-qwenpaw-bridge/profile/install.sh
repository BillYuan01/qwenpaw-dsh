#!/bin/sh
# Install the `qwenpaw` DSH profile that uses @local/dsh-qwenpaw-bridge.
#
#   DSH_HOME=~/.dsh ./profile/install.sh
#
# The script never overwrites an existing cordis.patch.yml, so a deployment's
# model route survives re-runs. It symlinks the bridge package into the
# profile's node_modules (the same layout `dsh plugin install` would produce)
# and writes the absolute `file:` dependency.
set -eu

BRIDGE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
DSH_HOME=${DSH_HOME:-"$HOME/.dsh"}
PROFILE_DIR="$DSH_HOME/profiles/qwenpaw"

mkdir -p "$PROFILE_DIR/node_modules/@local"
ln -sfn "$BRIDGE_DIR" "$PROFILE_DIR/node_modules/@local/dsh-qwenpaw-bridge"

cat > "$PROFILE_DIR/package.json" <<JSON
{
  "name": "dsh-profile-qwenpaw",
  "private": true,
  "dependencies": {
    "@local/dsh-qwenpaw-bridge": "file:$BRIDGE_DIR"
  },
  "dsh": {
    "profile": {
      "bundles": [
        "@deepseek-ai/dsh-base",
        "@deepseek-ai/dsh-acp-app",
        "@local/dsh-qwenpaw-bridge"
      ]
    }
  }
}
JSON

if [ ! -f "$PROFILE_DIR/cordis.patch.yml" ]; then
  cp "$BRIDGE_DIR/profile/cordis.patch.yml" "$PROFILE_DIR/cordis.patch.yml"
  echo "wrote sample $PROFILE_DIR/cordis.patch.yml (edit the model route)"
fi
if [ ! -f "$PROFILE_DIR/cordis.yml" ]; then
  echo '[]' > "$PROFILE_DIR/cordis.yml"
fi
if [ ! -f "$PROFILE_DIR/pnpm-workspace.yaml" ]; then
  printf 'packages:\n  - .\n\nnodeLinker: hoisted\nautoInstallPeers: false\n' > "$PROFILE_DIR/pnpm-workspace.yaml"
fi

echo "installed qwenpaw profile at $PROFILE_DIR"
echo "boot it with: DSH_HOME=$DSH_HOME dsh --profile qwenpaw"
