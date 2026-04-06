---
name: frugal-coder
description: >
  Delegate coding, text generation, architecture design, code review, debugging, and other cognitive tasks
  to a cheaper/free model via Aider or direct API calls, saving the main OpenClaw model's tokens.
  Use this skill proactively when: (1) writing or editing code files, (2) generating documentation or comments,
  (3) explaining code or errors, (4) designing architectures or APIs, (5) brainstorming solutions,
  (6) translating or summarizing technical content, (7) any task where a cheaper model suffices.
  Do NOT use for: simple file reads, git operations, system commands, or multi-tool orchestration
  that requires the main model's tool-calling ability.
---

# Frugal Coder Skill

Delegate cognitive work to a cheaper/free model to save the main model's tokens.

## Architecture

```
OpenClaw (main model, tool-calling capable)
  │
  ├── Cognitive tasks → frugal-gateway → cheap model (free/low-cost)
  │   ├── Code generation/editing (via Aider)
  │   ├── Text Q&A / explanation
  │   ├── Architecture design
  │   └── Code review / debugging
  │
  └── System tasks → OpenClaw tools (exec, read, write, etc.)
      ├── File system operations
      ├── Git operations
      ├── Running tests/commands
      └── Multi-step coordination
```

## Quick Start

### 1. Start the Gateway

```bash
# Edit config.yaml first, then:
python3 scripts/frugal-gateway.py --config config.yaml

# Or use environment variables:
FRUGAL_API_BASE=https://your-api/v1 FRUGAL_API_KEY=sk-xxx FRUGAL_MODEL=model-id \
  python3 scripts/frugal-gateway.py --port 4010
```

### 2. Verify

```bash
curl -s http://127.0.0.1:4010/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"any","messages":[{"role":"user","content":"say hi"}],"max_tokens":10}' | python3 -m json.tool
```

## Delegation Patterns

### Pattern 1: Text Q&A (cheapest)

For explanations, translations, summaries, brainstorming:

```bash
# Via gateway directly
curl -s http://127.0.0.1:4010/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"x","messages":[{"role":"user","content":"YOUR_QUESTION"}],"max_tokens":4096,"stream":false}' \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['choices'][0]['message']['content'])"
```

Or use the helper:
```bash
python3 scripts/frugal-ask.py "你的问题"
```

### Pattern 2: Code Generation via Aider (free coding)

For writing, editing, refactoring code:

```bash
cd /path/to/project  # must be a git repo

OPENAI_API_BASE="http://127.0.0.1:4010/v1" \
OPENAI_API_KEY="any" \
aider --model openai/$FRUGAL_MODEL --no-stream --no-show-model-warnings \
  --message "YOUR_CODE_REQUEST" --yes-always --no-auto-commits \
  file1.py file2.py
```

Key Aider flags:
- `--no-stream` — REQUIRED (gateway aggregates SSE)
- `--no-show-model-warnings` — suppress unknown model warnings
- `--no-auto-commits` — let user review before committing
- `--edit-format whole` — for models without tool calling
- `--edit-format diff` — for models that follow diff format well

### Pattern 3: Complex Task Decomposition (main model + cheap model)

For complex multi-step tasks:

1. **Main model** decomposes the task into steps (uses tool-calling to coordinate)
2. **Cheap model** handles each cognitive step (code, design, analysis)
3. **Main model** executes system operations (mkdir, git, test)
4. **Cheap model** reviews results and suggests fixes

Example workflow:
```
User: "Add Redis caching to the Flask app"

Step 1 [main]: Decompose → "Need cache.py, update server.py, pip install redis"
Step 2 [cheap]: Generate cache.py code via Aider
Step 3 [main]: exec("pip install redis"), write files
Step 4 [cheap]: Review via grok-ask, suggest improvements
Step 5 [main]: Run tests, commit
```

## Decision Rules

When receiving a task, apply these rules:

| Task | Delegate to | Main model action |
|------|-------------|-------------------|
| Write/edit code | Aider + cheap model | Only write file and test |
| Explain code/concept | Cheap model Q&A | Forward response |
| Design architecture | Cheap model Q&A | Review and coordinate |
| Code review | Cheap model Q&A | Forward results |
| Debug errors | Cheap model analysis | Execute fix commands |
| Create directory | Main model exec | `mkdir -p` directly |
| Git operations | Main model exec | `git add/commit/push` |
| Run tests | Main model exec | `python pytest` etc. |
| Multi-step coordination | Main model plans, cheap model executes each step | Orchestrate |

## Configuration

### Switching Providers

Edit `config.yaml` or use environment variables:

```yaml
# Example: Switch to local Ollama
provider:
  api_base: "http://localhost:11434/v1"
  api_key: "ollama"
  model: "qwen2.5-coder:7b"
  fix_sse: false    # Ollama properly supports stream:false
  strip_tools: true  # But still no tool calling

# Example: Switch to DeepSeek
provider:
  api_base: "https://api.deepseek.com/v1"
  api_key: "sk-your-deepseek-key"
  model: "deepseek-coder"
  fix_sse: false
  strip_tools: false  # DeepSeek supports tool calling
```

### Multiple Providers

Create separate config files and run multiple gateways:

```bash
# Terminal 1: Grok (free, port 4010)
python3 scripts/frugal-gateway.py --config config-grok.yaml

# Terminal 2: Ollama (local, port 4011)
python3 scripts/frugal-gateway.py --config config-ollama.yaml --port 4011
```

Then choose which gateway to use based on task complexity.

## Troubleshooting

| Issue | Cause | Fix |
|-------|-------|-----|
| Aider says "Empty response" | Gateway not running | Start gateway first |
| Aider doesn't apply edits | Model output format issue | Try `--edit-format whole` |
| Gateway returns 502 | Upstream API down | Check provider status |
| SSL errors | Self-signed cert | Gateway handles this automatically |
| Port in use | Another instance running | Kill old process or use `--port` |

## Files

- `scripts/frugal-gateway.py` — Unified API gateway (SSE fix, tool stripping, routing)
- `scripts/frugal-ask.py` — Quick text Q&A helper
- `config.yaml` — Provider configuration (user-editable)
