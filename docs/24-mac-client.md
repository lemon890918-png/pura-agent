# macOS Client for pure-agent (Phase 12)

A Codex-style desktop client for pure-agent, packaged for macOS Apple Silicon.

## TL;DR — How to launch

```bash
# From Finder: double-click
/Users/wenxin/work/pure-agent/pure-agent-client.command

# From Terminal:
/Users/wenxin/work/pure-agent/run-client.sh
```

Both launch the same Electron app. The first time macOS will ask permission to open
a downloaded file (click Open).

## What you get

A native macOS window with:
- Custom P icon (blue gradient) in the title bar and Dock
- macOS hidden inset title bar (`titleBarStyle: 'hiddenInset'`)
- Vibrancy under-window blur (`vibrancy: 'under-window'`)
- Dark theme matching pure-agent palette (`#0e0e10`)

## UI Layout (Codex parity)

Left navigation rail (60px wide):
- 💬 Chat (active by default)
- ⚡ Skills — lazy-loads `GET /skills` (lists ~/.pure-agent/skills + project skills)
- ⏰ Automation — lazy-loads `GET /automations` (Hermes cron jobs)
- 📁 Files — lazy-loads `GET /files?path=...` (recursive project tree)
- ⌨️ Terminal — placeholder
- ⚙️ Settings — bottom-anchored, shows `/config` model + API key

Dynamic sidebar (260px wide, switches with nav):
- Chat → sessions list + `+ New session`
- Skills → discoverable skills from `~/.pure-agent/skills`
- Automation → cron jobs
- Files → project directory tree
- Settings → model + work-directory config

Main area with 4 tabs (Codex parity):
- 💬 Chat — `POST /sessions/{id}/chat` (REST, full round-trip)
- 🔄 Diff — `GET /diff` with +/- line coloring
- ⌨️ Terminal — `POST /terminal/run` (shlex-split, 30s timeout)
- 👁️ Preview — iframe placeholder for app preview

## Architecture

`main.js` (Electron main process):
1. Spawns the pure-agent gateway backend on startup:
   `.venv/bin/python3 -m pure_agent.server.gateway`
2. Waits 1.5s for the gateway to bind port 18790
3. Creates the BrowserWindow and loads `ui/index.html`
4. Tray icon (optional, only if `ui/icon.png` exists)

The renderer (ui/index.html) talks to the gateway over HTTP/REST. All four
endpoints wired up:
- `GET /health` — header status (5s poll)
- `GET/POST /sessions` — session list / create
- `GET /sessions/{id}` — message history
- `POST /sessions/{id}/chat` — round-trip agent chat
- `GET /files?path=` — directory listing (recursive)
- `GET /skills` — discoverable skills (Phase 12 addition)
- `GET /automations` — Hermes cron jobs (Phase 12 addition)
- `GET /diff` — git diff of project root
- `POST /terminal/run` — shlex-split shell exec (30s timeout)
- `GET /config` — model + project root config

## Why two launchers (.command and .sh)

`run-client.sh` runs Electron directly against the source repo — same way `npm
start` works, but with API-key sourcing from `~/.zshrc`.

`.command` is the Finder-double-clickable version. macOS Launch Services opens
it in Terminal.app, sources your shell rc, and `exec`s Electron. No icon-cache
issues, no codesign weirdness.

## What does NOT work (yet)

The packaged `.app` (electron-builder mac bundle) **fails to create a renderer**
on this machine: main process spawns, GPU + Network helpers spawn, but the
renderer never does. Sample stack showed V8 stuck in
`CompilationDependencies::FieldTypeDependencyOffTheRecord` — likely a JIT issue
specific to Electron 33.4.11 on this Mac (M-series, macOS 26.5.1).

Workaround in this iteration: distribute the `.command` launcher instead of the
.app bundle. The .command path is stable and proven to work.

**Files preserved for future debugging**:
- `dist/pure-agent-1.0.0-arm64.dmg` (237 MB) — packaged but buggy
- `dist/pure-agent-1.0.0-arm64-mac.zip` (234 MB) — packaged but buggy
- `dist/pure-agent.app.broken-pkg/` — the broken bundle

**Future fix paths** (try in order):
1. Upgrade Electron to 35.x or 36.x
2. Try `electron-forge` instead of `electron-builder`
3. Add `--disable-features=VizDisplayCompositor` to packaged binary launch args
4. Try the Sequoia electron-builder recipe with `extendInfo: { NSAppTransportSecurity: ... }`

## Files

| Path | Purpose |
|---|---|
| `/Users/wenxin/work/pure-agent/main.js` | Electron main process |
| `/Users/wenxin/work/pure-agent/preload.js` | IPC bridge (currently unused) |
| `/Users/wenxin/work/pure-agent/package.json` | Electron-builder config |
| `/Users/wenxin/work/pure-agent/ui/index.html` | Single-file UI (693 lines) |
| `/Users/wenxin/work/pure-agent/ui/icon.png` | P icon (512×512 PNG) |
| `/Users/wenxin/work/pure-agent/ui/icon.icns` | macOS icon bundle |
| `/Users/wenxin/work/pure-agent/ui/icon.iconset/` | Source PNG set |
| `/Users/wenxin/work/pure-agent/run-client.sh` | Linux-style launcher |
| `/Users/wenxin/work/pure-agent/pure-agent-client.command` | Finder-double-click launcher |
| `/Users/wenxin/work/pure-agent/src/pure_agent/server/gateway.py` | + `/skills`, `/automations` endpoints |
| `/tmp/gen_icons.py` | Icon generator (Pillow + iconutil) |

## Verifying it works

After launching:
- Status bar (top right) shows `v0.1.0 · N sessions · Ns` (backend healthy)
- Click `+ New session` to create a session
- Type into the chat box, press Enter, watch the agent respond
- Click `Diff` tab → see git diff with red/green coloring
- Click `Skills` in nav rail → see loaded skills from `~/.pure-agent/skills`
- Click `Files` in nav rail → navigate project directory tree

## Logs

Launch log:
```
~/Library/Application Support/pure-agent/pure-agent-launch.log
```

Gateway logs go to stdout/stderr of the .venv Python subprocess (visible in
`.command` Terminal window).