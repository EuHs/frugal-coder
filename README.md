# 🪙 Frugal Coder

**通用智能路由中间件** — 让 OpenClaw 把大多数请求委托给便宜/免费模型，主模型只在真正必要时才调用。

---

## 核心特性

- **三级路由** — SIMPLE / COMPLEX / NEEDS_TOOLS 自动分类
- **ReAct 编排器** — 复杂任务多步推理，全程便宜模型
- **模型无关** — 支持任意 OpenAI-compatible API（Grok、Ollama、DeepSeek、Qwen 等）
- **SSE 修复代理** — 聚合流式响应为标准 JSON
- **每日审计** — 自动分析 token 节省量

---

## 架构

```
用户消息 → smart-router:4020 (智能路由网关)
              │
              ▼ 便宜模型分类（~200 tokens）
    ┌─────────┼──────────┐
    ↓         ↓          ↓
SIMPLE    COMPLEX    NEEDS_TOOLS
(~70%)    (~20%)      (~10%)
    ↓         ↓          ↓
 便宜模型   ReAct编排   便宜模型
 直接回     多步推理    (精简上下文)
  免费       免费        免费
```

```
OpenClaw (主模型)
  └── 所有请求 → smart-router → 三级分类 → 路由
```

---

## 安装

```bash
# 克隆到 skills 目录
git clone https://github.com/EuHs/frugal-coder.git ~/.openclaw/skills/frugal-coder

# 一键安装（配置 API）
cd ~/.openclaw/skills/frugal-coder
bash install.sh
```

---

## 启动服务

```bash
# SSE 修复代理（上游流式 → 标准 JSON）
python3 scripts/sse-fix-proxy.py --port 4010 \
  --upstream https://YOUR_UPSTREAM_API/v1 \
  --api-key YOUR_API_KEY

# 智能路由网关（拦截所有 OpenClaw 请求）
python3 scripts/smart-router.py --port 4020 --cheap-model YOUR_MODEL

# OpenClaw 配置使用 smart-router 作为主模型
openclaw restart
```

### 开机自启（macOS launchd）

```bash
# 已自动加载：
launchctl list | grep -E "Smart|Frugal"
```

---

## 配置

编辑 `config.yaml`：

```yaml
provider:
  api_base: "https://your-cheap-model-api/v1"
  api_key: "your-api-key"
  model: "your-model-name"        # grok-4.1-fast / deepseek-coder / qwen2.5:7b / ...

gateway:
  port: 4010                       # SSE 修复代理端口
router:
  port: 4020                       # 智能路由端口
```

---

## 使用方法

安装并启动后，**所有 OpenClaw 对话自动经过智能路由**：

```
# 简单对话 → 便宜模型直接回（免费）
今天天气怎么样？

# 复杂分析 → ReAct 编排器多步推理（免费）
深度分析特斯拉最新财报，对比比亚迪

# 工具类请求 → 便宜模型处理
帮我执行 git status
```

---

## 手动使用

```bash
# 简单问答
python3 scripts/frugal-ask.py "解释什么是闭包"

# ReAct 编排器（复杂任务）
python3 scripts/react-orchestrate.py "分析英伟达投资价值"

# 每日审计（分析 token 节省量）
python3 scripts/frugal-audit.py

# 任务分类测试
python3 scripts/thinking-classifier.py "写一个快速排序"
```

---

## 文件结构

```
frugal-coder/
├── SKILL.md                    # OpenClaw skill 定义
├── README.md                   # 本文件
├── install.sh                  # 安装脚本
├── config.yaml                 # 默认配置
└── scripts/
    ├── smart-router.py         # 智能路由网关（三级分类）
    ├── react-orchestrate.py    # ReAct 编排器
    ├── sse-fix-proxy.py        # SSE→JSON 修复代理
    ├── frugal-ask.py           # 简单问答
    ├── frugal-audit.py         # 每日 token 审计
    └── thinking-classifier.py  # 任务分类引擎
```

---

## 路由统计

```bash
curl http://127.0.0.1:4020/stats
```

```json
{
  "total_requests": 100,
  "cheap_handled": 72,
  "react_handled": 20,
  "main_handled": 8,
  "errors": 0
}
```

---

## 故障排除

| 问题 | 解决 |
|------|------|
| 502 / "all models unavailable" | 检查上游 API 是否可用，重启 `sse-fix-proxy` |
| NEEDS_TOOLS 失败 | 上游多消息限制，smart-router 会自动合并为单消息 |
| 重启后服务消失 | `launchctl list \| grep -E "Smart\|Frugal"` 检查守护进程 |
| 主模型仍被调用 | 检查 `cheap_handled` vs `main_handled` 统计比例 |
