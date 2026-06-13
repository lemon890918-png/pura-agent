#!/bin/bash
# Launch pure-agent dev client.
# This is a packaged-style entry point for the pure-agent desktop client.
# It launches the Electron app from the source repo.

set -e

REPO="/Users/wenxin/work/pure-agent"
ELECTRON="$REPO/node_modules/electron/dist/Electron.app/Contents/MacOS/Electron"

if [ ! -x "$ELECTRON" ]; then
  echo "ERROR: Electron binary not found at $ELECTRON"
  echo "Run: cd $REPO && npm install"
  exit 1
fi

# Source API keys from shell rc (best effort)
for rc in "$HOME/.zshrc" "$HOME/.bashrc" "$HOME/.bash_profile"; do
  if [ -f "$rc" ]; then
    set +e
    source "$rc" 2>/dev/null
    set -e
  fi
done

cd "$REPO"
exec "$ELECTRON" .