#!/usr/bin/env bash
# push.sh — 一键推送 pure-agent 到 GitHub
#
# 凭据策略：git osxkeychain helper。token 加密存在 macOS Keychain 里，
# 脚本本身不接触 token，也不写进 .git/config。第一次用需要交互输入 token，
# 之后直接跑脚本就 push。
#
# 用法：
#   bash scripts/push.sh                   # 推送当前 main
#   bash scripts/push.sh --branch dev      # 推送别的分支
#   bash scripts/push.sh --setup           # 只配置 credential helper（不 push）
#
# 网络说明：
#   github.com:443 在你网络里被防火墙 drop，必须走 127.0.0.1:9567 代理。
#   api.github.com 没被拦，但 git push 走的是 github.com。
set -euo pipefail

BRANCH="main"
SETUP_ONLY=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --branch) BRANCH="$2"; shift 2 ;;
        --setup)  SETUP_ONLY=true; shift ;;
        -h|--help)
            sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

# 代理（你本机 github.com:443 被防火墙 drop，没这个 push 不过）
export https_proxy="http://127.0.0.1:9567"
export http_proxy="http://127.0.0.1:9567"

# 凭据 helper — token 存进 Keychain（macOS 自带加密）
git config --global credential.helper osxkeychain
git config --global credential.useHttpPath true

if $SETUP_ONLY; then
    echo "✓ credential.helper = osxkeychain"
    echo "✓ useHttpPath = true"
    echo "下次 push 时会弹一次密码框让你输入 token，之后存 Keychain 永久免输"
    exit 0
fi

cd "$(dirname "$0")/.."

# 确认仓库
REMOTE_URL=$(git remote get-url origin 2>/dev/null || echo "")
if [[ "$REMOTE_URL" != *"github.com/lemon890918-png/pura-agent"* ]]; then
    echo "ERROR: remote origin 不是 lemon890918-png/pura-agent"
    echo "  当前: $REMOTE_URL"
    exit 1
fi

# 当前状态
echo "==> 推送前状态："
git status --short
echo
echo "==> 待推 commits（origin/$BRANCH..HEAD）："
git log --oneline "origin/$BRANCH..HEAD" 2>/dev/null || echo "  (没有 origin/$BRANCH，本地首次推送)"
echo

echo "==> git push origin $BRANCH ..."
git push origin "$BRANCH"

echo
echo "==> 推送完成。当前 main 最新 3 个 commit："
git log --oneline -3