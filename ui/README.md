# pure-agent UI

Minimal web UI for pure-agent. Connects to the gateway over HTTP.

## Run

```bash
# 1. Start the gateway
pure-agent serve start --port 18790

# 2. Open the UI
open ui/index.html
# or serve it:
python3 -m http.server 3001 --directory ui
```

## What it does

- Lists sessions in the left sidebar
- Click a session to load its messages
- Type a message in the input bar and press Enter
- The agent responds via the gateway's `/chat` endpoint
- WebSocket support is planned for Phase 9 (streaming)

## Configuration

By default the UI connects to `http://127.0.0.1:18790`. To change this, edit
the `gateway` and `wsBase` variables at the top of the inline `<script>` block.
