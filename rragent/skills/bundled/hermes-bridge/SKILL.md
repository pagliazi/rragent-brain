---
name: hermes-bridge
description: Delegate complex tasks to Hermes Agent — web search, code execution, browser automation, file operations, and 118 built-in skills
version: 1.0.0
metadata:
  rragent:
    requires:
      env: ["REDIS_URL"]
      bins: ["python3", "redis-cli"]
    primaryEnv: "REDIS_URL"
    emoji: "\U0001f980"
    os: ["macos", "linux"]
---

# Hermes Agent Bridge

Delegate tasks to Hermes Agent when you need capabilities beyond RRAgent's native toolset.

## When to Use

- **Web search & research**: Hermes has deep web extraction and multi-source search
- **Code execution**: Hermes supports PTC (Programmatic Tool Calling) — write Python scripts that chain tool calls in a single turn
- **File operations**: Read, write, edit, search files on the Hermes host
- **Terminal commands**: Execute shell commands on the Hermes server
- **Browser automation**: Full Chrome/Chromium automation (click, fill, screenshot, navigate)
- **Image generation/analysis**: Generate or analyze images via model APIs
- **Complex multi-step tasks**: Hermes runs up to 30 iterations of reasoning + tool calling
- **Self-improving workflows**: Hermes learns from completed tasks and creates reusable skills

## How It Works

The bridge uses Redis Pub/Sub to communicate with Hermes. Messages are sent to `bridge:rragent→hermes` and replies come back on a dedicated per-message channel.

## Procedure

1. **Delegate a full task**:
   Publish to the bridge with action `delegate_task`:
   ```json
   {
     "action": "delegate_task",
     "params": {
       "prompt": "Search the web for recent AI chip news and write a summary report",
       "toolsets": ["core", "web", "terminal"],
       "max_iterations": 30
     }
   }
   ```

2. **Call a single Hermes tool**:
   Use action `call_tool` for direct tool invocation:
   ```json
   {
     "action": "call_tool",
     "params": {
       "tool": "web_search",
       "arguments": {"query": "latest AI chip developments 2026"}
     }
   }
   ```

3. **Search Hermes skills**:
   Use action `search_skills`:
   ```json
   {
     "action": "search_skills",
     "params": {"query": "data analysis", "limit": 10}
   }
   ```

4. **Search Hermes memory**:
   Use action `query_memory`:
   ```json
   {
     "action": "query_memory",
     "params": {"query": "previous research on semiconductors"}
   }
   ```

## Available Hermes Toolsets

| Toolset | Tools | Description |
|---------|-------|-------------|
| `core` | terminal, read_file, write_file, patch, search_files, process | File and system operations |
| `web` | web_search, web_extract | Web search and content extraction |
| `browser` | browser_navigate, browser_click, browser_type, browser_scroll, vision_analyze | Full browser automation |
| `media` | image_generate, image_analyze, text_to_speech | Media generation and analysis |
| `delegation` | delegate, execute_code | Sub-agent spawning and PTC |
| `automation` | cron, webhook | Scheduled and event-driven tasks |

## Pitfalls

- Hermes tasks may take up to 5 minutes for complex multi-step workflows
- PTC (code execution) requires a sandbox environment to be configured
- Browser automation requires Chrome/Chromium installed on the Hermes host
- Skills learned by Hermes persist on the Hermes side — use skill sync to share with RRAgent

## Verification

Check bridge health:
```bash
redis-cli HGET bridge:heartbeats hermes-bridge
```

Expected: JSON with recent timestamp, PID, and component name.
