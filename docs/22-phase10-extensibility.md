# Phase 10 — Configurable Skills + MCP + Web Search 详细设计

═══════════════════════════════════════════════
0. 目标
═══════════════════════════════════════════════

让 pure-agent 支持 3 类可插拔扩展：

  1. **Skills** （来自 skills.sh 风格）
     - 每个 skill = 一个 SKILL.md (description + procedural knowledge)
     - 注入到 system prompt 的 "Active Skills" 段
     - 来自本地 `~/.pure-agent/skills/<skill-name>/SKILL.md` 或
       `npx skills add <owner/repo>` 后的目录
     - 启动时自动加载已安装的 skills

  2. **MCP servers** （Model Context Protocol）
     - 从 `~/.pure-agent/mcp.json` 读 server 列表
     - 启动时 spawn `mcp-server` 子进程，通过 stdio JSON-RPC 通讯
     - 把每个 MCP tool 注册到 ToolRegistry
     - 失败 fallback 到本地 tool

  3. **Web Search providers** （已部分实现）
     - 加更多 provider: Brave / Tavily / DDG / Exa / SerpAPI
     - 优先级 + 失败 fallback
     - 在 config 里可单独 enable/disable

═══════════════════════════════════════════════
1. 架构
═══════════════════════════════════════════════

```
~/.pure-agent/
  config.yaml          # 主配置
    web_search:
      providers: [tavily, brave, ddg]
      max_results: 5
      timeout_s: 30
    skills:
      auto_load: true
      skills_dir: ~/.pure-agent/skills
    mcp:
      enabled: true
      servers:
        - name: filesystem
          command: npx -y @modelcontextprotocol/server-filesystem /tmp
        - name: github
          command: npx -y @modelcontextprotocol/server-github
          env:
            GITHUB_TOKEN: ${GITHUB_TOKEN}
  skills/
    tavily-search/
      SKILL.md
    python-performance-optimization/
      SKILL.md
  mcp.json             # (optional) mcp servers list
```

═══════════════════════════════════════════════
2. Skill 格式
═══════════════════════════════════════════════

每个 skill 是一个目录：

```
skills/
  my-skill/
    SKILL.md            # 必须
    scripts/            # 可选
    references/         # 可选
```

SKILL.md 格式：

```markdown
---
name: tavily-search
description: Use Tavily API for high-quality web search with content extraction.
version: 1.0
source: tavily-ai/skills
allowed_tools: [web_search, web_fetch]
---

# Tavily Search

Use this skill when the user asks for a web search and you have a Tavily API key.

## When to use

- User asks "search the web for X"
- User asks "find information about X online"

## How to use

Call `web_search` with provider="tavily" or just default (Tavily is first priority).
Tavily returns clean, content-extracted snippets, ideal for summarization.

## Example

User: "Search for the latest Python 3.13 features"
→ Call: web_search(query="Python 3.13 new features", max_results=5)
→ Summarize top 3 results for the user.
```

启动时：

1. 扫描 `skills_dir` 下所有 `*/SKILL.md`
2. parse frontmatter (yaml)
3. 渲染到 system prompt 的 ## Active Skills section
4. agent 看到 skill 描述后能决定是否触发对应 tool

═══════════════════════════════════════════════
3. MCP 实现
═══════════════════════════════════════════════

3.1 JSON-RPC over stdio
    - 启动时 `subprocess.Popen(server_command, stdin=PIPE, stdout=PIPE)`
    - 用 `initialize` / `tools/list` / `tools/call` 协议
    - 跟 Anthropic 的 MCP spec 一致

3.2 MCPTool 适配器
    - 每个 MCP tool 包成 `pure_agent.tools.Tool` 兼容对象
    - 调 `tools/call` 时 spawn RPC
    - 错误传播

3.3 配置
    - mcp.json 标准格式:
    ```json
    {
      "mcpServers": {
        "filesystem": {
          "command": "npx -y @modelcontextprotocol/server-filesystem",
          "args": ["/tmp"]
        }
      }
    }
    ```

3.4 降级
    - 如果 `mcp` enabled 但 server 启动失败，warn + 跳过
    - 后续 retry 可加 exponential backoff

═══════════════════════════════════════════════
4. Web Search 增强
═══════════════════════════════════════════════

4.1 Provider config
    - 优先级在 config 里声明
    - 支持 add custom provider 走 REST API
    - 失败重试 + cache

4.2 已实现
    - DDG (no key)
    - Brave (BRAVE_API_KEY)
    - Tavily (TAVILY_API_KEY)  ← Phase 9+ 加的

4.3 可选加
    - Exa (EXA_API_KEY)
    - SerpAPI (SERPAPI_API_KEY)
    - Google Custom Search (GOOGLE_CSE_ID + GOOGLE_CSE_KEY)

═══════════════════════════════════════════════
5. CLI
═══════════════════════════════════════════════

  pure-agent skills list              # 列出已装 skills
  pure-agent skills add <owner/repo>  # 从 GitHub 装（clone）
  pure-agent skills show <name>       # 显示 SKILL.md 内容
  pure-agent skills remove <name>     # 删

  pure-agent mcp list                 # 列出已配置 MCP servers
  pure-agent mcp enable <name>        # 临时启用
  pure-agent mcp disable <name>       # 临时禁用
  pure-agent mcp test <name>          # 测试连通

  pure-agent search "query"           # 快速试 search provider
  pure-agent search --provider=tavily "query"

═══════════════════════════════════════════════
6. 验收清单
═══════════════════════════════════════════════

□ 6.1 SKILL.md loader 能 parse frontmatter
□ 6.2 Skills 渲染到 system prompt
□ 6.3 skills CLI: list / add / show / remove
□ 6.4 MCP server 启动 (stdio JSON-RPC)
□ 6.5 MCP tools 注入到 ToolRegistry
□ 6.6 MCP CLI: list / test
□ 6.7 Config: web_search / skills / mcp 段都支持
□ 6.8 端到端：装一个 skill + 启一个 MCP server + agent 看到
□ 6.9 Tests 290+ passing
□ 6.10 docs/22-phase10-extensibility.md 验收报告

═══════════════════════════════════════════════
7. 时间
═══════════════════════════════════════════════

单人: ~3-5 天
