# Frugal Coder - 架构设计

## 设计目标

让 OpenClaw 用户在日常使用中，将大部分认知工作（代码生成、文本问答、架构设计等）
自动路由到便宜的模型，从而大幅降低主模型的 token 消耗。

## 核心组件

### 1. frugal-gateway.py（统一 API 网关）

```
┌──────────────┐     ┌──────────────────┐     ┌──────────────┐
│  Aider       │────→│  frugal-gateway  │────→│  便宜模型 API  │
│  OpenClaw    │     │  · SSE→JSON 修复  │     │  (任意兼容API) │
│  curl        │     │  · 清理 tools     │     │              │
└──────────────┘     │  · 路由转发       │     └──────────────┘
                     └──────────────────┘
                           port 4010
```

**解决的问题：**
- 逆向代理 API（如 grok2api）强制返回 SSE 流式响应，即使请求 `stream: false`
- 便宜模型不支持 tool calling，但 Aider/litellm 会发送 `tools` 参数
- 不同 provider 的 API 格式略有差异

**核心功能：**
1. SSE 聚合：把 SSE chunk 流聚合成标准 OpenAI chat completion JSON
2. 参数清理：移除 `tools`, `tool_choice`, `response_format` 等不支持参数
3. 模型映射：自动替换请求中的 model ID 为配置的便宜模型
4. SSL 绕过：自动处理自签名证书问题

### 2. frugal-ask.py（快速问答）

直接通过网关向便宜模型提问，获取纯文本回答。
适用于：解释概念、翻译、总结、方案对比等纯文本任务。

### 3. Aider 集成

通过网关让 Aider 使用便宜模型进行代码编辑：
```bash
OPENAI_API_BASE=http://127.0.0.1:4010/v1 \
OPENAI_API_KEY=any \
aider --model openai/$MODEL --no-stream --no-show-model-warnings
```

**必须加 `--no-stream`**，否则 Aider 发送流式请求，网关处理会出问题。

## 任务路由逻辑

```
用户请求
  │
  ├── 简单文本问答 ──────→ frugal-ask.py → 便宜模型（免费）
  │
  ├── 代码编写/编辑 ─────→ Aider + 网关 → 便宜模型（免费）
  │
  ├── 架构设计/方案 ─────→ frugal-ask.py → 便宜模型（免费）
  │
  ├── 系统操作(mkdir等) ─→ OpenClaw exec（无 token 消耗）
  │
  ├── Git 操作 ─────────→ OpenClaw exec（无 token 消耗）
  │
  └── 复杂多步协调 ─────→ 主模型规划 + 便宜模型执行每步
```

## 配置系统

三层优先级（从高到低）：
1. **环境变量**：`FRUGAL_API_BASE`, `FRUGAL_API_KEY`, `FRUGAL_MODEL`, `FRUGAL_PORT`
2. **命令行参数**：`--api-base`, `--api-key`, `--model`, `--port`
3. **配置文件**：`config.yaml`

支持多个配置文件切换 provider：
```bash
python3 frugal-gateway.py --config config-grok.yaml     # 免费逆向代理
python3 frugal-gateway.py --config config-ollama.yaml    # 本地模型
python3 frugal-gateway.py --config config-deepseek.yaml  # 便宜云端
```

## 已知限制

1. **Aider 编辑格式**：不支持 tool calling 的模型必须用 `whole` 或 `diff` 格式，
   不能用默认的 `code` 格式（依赖 function calling）
2. **文件创建**：Aider 不能创建新文件到 git repo 外的路径，需要先 mkdir + git init
3. **流式问题**：即使加 `--no-stream`，部分逆向 API 仍返回 SSE（网关会自动处理）
4. **长请求**：大文件的完整重写可能超出便宜模型的 context window

## 兼容的 Provider 列表

| Provider | api_base | fix_sse | strip_tools | 备注 |
|----------|----------|---------|-------------|------|
| grok2api | 自建 | true | true | 逆向代理，强制 SSE |
| Ollama | http://localhost:11434/v1 | false | true | 本地模型 |
| DeepSeek | https://api.deepseek.com/v1 | false | false | 支持 tool calling |
| Qwen | https://dashscope.aliyuncs.com/compatible-mode/v1 | false | false | 支持 tool calling |
| LM Studio | http://localhost:1234/v1 | false | true | 本地模型 |
| text-generation-webui | http://localhost:5000/v1 | false | true | 本地模型 |
