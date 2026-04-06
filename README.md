# 🪙 Frugal Coder

让 OpenClaw 把认知任务（编码、问答、设计）委托给便宜/免费模型，节省主模型 token。

## 支持的模型

| 模型 | 费用 | 备注 |
|------|------|------|
| Grok (via grok2api) | 免费 | 需要逆向代理 |
| DeepSeek | 低费 | 支持 tool calling |
| Ollama (本地) | 免费 | 需要本地安装 |
| 其他 OpenAI API 兼容模型 | 取决于提供商 | |

## 一键安装

```bash
# 下载并运行安装脚本
curl -fsSL https://raw.githubusercontent.com/YOUR_USER/frugal-coder/main/install.sh | bash
```

或者手动安装：

```bash
# 1. 克隆到 skills 目录
git clone https://github.com/YOUR_USER/frugal-coder.git ~/.openclaw/skills/frugal-coder

# 2. 运行安装
cd ~/.openclaw/skills/frugal-coder
bash install.sh

# 3. 重启 OpenClaw
openclaw restart
```

## 配置 API

编辑 `config.yaml`：

```yaml
provider:
  api_base: "https://your-cheap-model-api/v1"
  api_key: "your-api-key"
  model: "model-name"
  fix_sse: true      # grok2api 等需要，其他提供商设 false
  strip_tools: true   # 不支持 tool calling 的模型设 true
```

### 使用 grok2api（免费）

如果使用 grok2api 逆向代理：

```yaml
provider:
  api_base: "https://www.tongxuanzn.icu/v1"
  api_key: "your-grok2api-key"
  model: "grok-4.1-fast"
  fix_sse: true
  strip_tools: true
```

## 使用方法

安装后，在 OpenClaw 对话中直接提出需求：

```
写一个回文检测函数
帮我解释这段代码的含义
设计一个 REST API 架构
翻译这段英文为中文
```

OpenClaw 会自动将认知任务委托给配置的便宜模型。

## 手动使用

### 文本问答
```bash
python3 ~/.openclaw/skills/frugal-coder/scripts/frugal-ask.py "你的问题"
```

### 代码编辑（通过 Aider）
```bash
cd your-project  # 必须是 git 仓库
OPENAI_API_BASE="http://127.0.0.1:4010/v1" \
OPENAI_API_KEY="any" \
aider --model openai/grok-4.1-fast --no-stream --no-show-model-warnings \
  --message "写一个 Calculator 类" yourfile.py
```

### 启动网关
```bash
python3 ~/.openclaw/skills/frugal-coder/scripts/grok2api-fix-proxy.py --port 4010
```

## 工作原理

```
OpenClaw (主模型，tool-calling)
  │
  ├── 认知任务 → 网关 → 便宜模型（省 token）
  │   ├── 代码生成/编辑 → Aider
  │   ├── 文本问答 → 直接 API
  │   └── 设计/分析 → 直接 API
  │
  └── 系统任务 → OpenClaw exec/read/write
```

## 文件结构

```
frugal-coder/
├── SKILL.md              # Skill 定义（OpenClaw 自动加载）
├── install.sh            # 一键安装脚本
├── README.md             # 本文件
├── config.yaml            # 默认配置
├── config-grok.yaml      # Grok 专用配置示例
└── scripts/
    ├── grok2api-fix-proxy.py  # SSE→JSON 网关
    ├── frugal-gateway.py      # 统一网关
    └── frugal-ask.py         # 文本问答工具
```

## 故障排除

| 问题 | 解决 |
|------|------|
| "Empty response" | 网关未启动，先运行 `python3 scripts/grok2api-fix-proxy.py --port 4010` |
| 端口被占用 | `lsof -ti:4010 | xargs kill -9` 然后重启 |
| Aider 不生效 | 确认 `--no-stream` 参数 |
| Token 仍然消耗 | 检查路由规则，确认任务类型 |

## GitHub 发布

```bash
# 1. 创建 GitHub repo
gh repo create frugal-coder --public --clone

# 2. 推送
cd ~/.openclaw/skills/frugal-coder
git remote set-url origin https://github.com/YOUR_USER/frugal-coder.git
git push -u origin main
```
