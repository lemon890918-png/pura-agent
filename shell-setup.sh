# pure-agent shell integration
# Source this file to get 'pa' shortcut + auto-loaded env:
#   source /Users/wenxin/work/pure-agent/shell-setup.sh

# Load keys from ~/.hermes/.env
if [ -f "$HOME/.hermes/.env" ]; then
    export MINIMAX_API_KEY=$(grep '^MINIMAX_API_KEY=*** "$HOME/.hermes/.env" | cut -d= -f2-)
fi

# Tavily key (Phase 10+)
export TAVILY_API_KEY="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"

# Make pure-agent in PATH (uv tool install puts it here)
case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *) export PATH="$HOME/.local/bin:$PATH" ;;
esac

# Shortcuts
alias pa='pure-agent chat --model MiniMax-M3'
alias pa-init='pure-agent init'
alias pa-status='pure-agent status'
alias pa-skills='pure-agent skills list'
alias pa-mcp='pure-agent mcp list'
alias pa-ui='pure-agent serve start --port 18790'

# Confirmation
echo "✓ pure-agent ready. Type 'pa' to start chatting."
