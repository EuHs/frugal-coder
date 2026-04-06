#!/bin/bash
# frugal-coder install.sh - 一键安装 frugal-coder skill 到 OpenClaw
set -e

SKILL_DIR="$HOME/.openclaw/skills/frugal-coder"
OPENCLAW_DIR="$HOME/.openclaw"

echo "🪙 Frugal Coder 安装脚本"
echo "========================"

# 1. 确认 OpenClaw 已安装
if ! command -v openclaw &> /dev/null; then
    echo "❌ 找不到 openclaw 命令，请先安装 OpenClaw"
    exit 1
fi

# 2. 确认 pip3
if ! command -v pip3 &> /dev/null; then
    echo "❌ 找不到 pip3"
    exit 1
fi

# 3. 安装 Aider（如果尚未安装）
if ! command -v aider &> /dev/null; then
    echo "📦 安装 Aider..."
    pip3 install aider-chat
else
    echo "✅ Aider 已安装"
fi

# 4. 确认 git
if ! command -v git &> /dev/null; then
    echo "❌ 找不到 git"
    exit 1
fi

# 5. 复制 skill 到 OpenClaw skills 目录
echo "📁 安装 skill 到 $SKILL_DIR..."
mkdir -p "$OPENCLAW_DIR/skills"
cp -r "$(dirname "$0")" "$SKILL_DIR"

# 5b. 自动注入路由表到 AGENTS.md
echo "📝 注入路由规则到 AGENTS.md..."
WORKSPACE_DIR="$OPENCLAW_DIR/workspace"
AGENTS_MD="$WORKSPACE_DIR/AGENTS.md"

if [ -f "$AGENTS_MD" ]; then
    if grep -q "Frugal Coder" "$AGENTS_MD" 2>/dev/null; then
        echo "  (路由规则已存在，跳过)"
    else
        cat >> "$AGENTS_MD" << 'ROUTING'

## 🪙 Frugal Coder - 省钱优先规则

**所有认知类任务优先委托给便宜/免费模型（通过 frugal-coder skill），只在必要时使用主模型。**

### 路由规则（按顺序判断）

1. **代码编写/编辑/重构** → 通过 Aider + 网关调用便宜模型（免费）
2. **文本问答/解释/翻译/总结** → 通过 `frugal-ask.py` 调用便宜模型（免费）
3. **架构设计/方案讨论/代码 Review** → 通过 `frugal-ask.py` 调用便宜模型（免费）
4. **错误分析/调试建议** → 通过 `frugal-ask.py` 调用便宜模型（免费）
5. **系统操作**（mkdir, git, 运行命令）→ 主模型 exec 直接执行
6. **复杂多步任务** → 主模型做规划 + 便宜模型执行每步认知工作
7. **简单闲聊/确认** → 主模型直接回复
8. **复杂闲聊/深度讨论** → 便宜模型生成初稿，主模型润色回复

### 执行方式

- 网关地址：`http://127.0.0.1:4010`
- Aider 编码：`OPENAI_API_BASE=http://127.0.0.1:4010/v1 OPENAI_API_KEY=any aider --no-stream --no-show-model-warnings`
- 文本问答：`python3 ~/.openclaw/skills/frugal-coder/scripts/frugal-ask.py "问题"`
- 网关必须先启动：`python3 ~/.openclaw/skills/frugal-coder/scripts/grok2api-fix-proxy.py --port 4010`

### 什么时候用主模型

- 需要 tool calling 的任务（exec, read, write, browser 等工具）
- 多步协调（规划完需要逐步执行并验证）
- 需要理解对话上下文的任务
- 发送消息/邮件等外部操作

### Provider 切换

编辑 `~/.openclaw/skills/frugal-coder/config.yaml` 或 `~/.aider.conf.yml` 切换便宜模型。
当前默认：YOUR_MODEL（通过 grok2api 逆向代理，免费）
ROUTING
        echo "  ✅ 路由规则已注入 AGENTS.md"
    fi
else
    echo "  ⚠️ 未找到 AGENTS.md，跳过路由注入（可在安装后手动添加）"
fi

# 5c. 生成 ~/.aider.conf.yml（从 config.yaml 读取模型）
echo "⚙️  生成 Aider 配置..."
MODEL_NAME=$(sed -n 's/^  model: *["'"'"']//p' "$SKILL_DIR/config.yaml" 2>/dev/null | head -1 | tr -d '"'"'"' | xargs)
[ -z "$MODEL_NAME" ] && MODEL_NAME="YOUR_MODEL"

cat > "$HOME/.aider.conf.yml" << AIDER_CONFIG
# Aider 默认配置 - 配合 frugal-coder 网关使用
# 网关必须先启动（端口 4010）

model: openai/$MODEL_NAME
no-stream: true
no-show-model-warnings: true
no-auto-commits: true
AIDER_CONFIG
echo "  ✅ Aider 配置已生成（model: $MODEL_NAME）"

# 6. 配置 OpenClaw 加载 skill
echo "⚙️  配置 OpenClaw..."
CONFIG="$OPENCLAW_DIR/openclaw.json"
if [ -f "$CONFIG" ]; then
    if grep -q '"extraDirs"' "$CONFIG"; then
        echo "  (openclaw.json extraDirs 已存在，跳过)"
    else
        python3 -c "
import json, sys
config_path = '$CONFIG'
with open(config_path, 'r') as f:
    config = json.load(f)
config.setdefault('skills', {})
config['skills'].setdefault('load', {})
config['skills']['load']['extraDirs'] = ['~/.openclaw/skills']
with open(config_path, 'w') as f:
    json.dump(config, f, indent=2, ensure_ascii=False)
print('已更新 openclaw.json')
"
    fi
else
    echo "  (未找到 openclaw.json，跳过配置)"
fi

# 7. 启动网关（后台）
echo "🚀 启动 frugal-gateway..."
nohup python3 "$SKILL_DIR/scripts/grok2api-fix-proxy.py" --port 4010 > /tmp/frugal-gateway.log 2>&1 &
sleep 2

# 8. 验证网关
if curl -s -m 3 http://127.0.0.1:4010/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{"model":"YOUR_MODEL","messages":[{"role":"user","content":"ok"}],"max_tokens":5}' \
    | grep -q "ok"; then
    echo "✅ 网关运行正常（端口 4010）"
else
    echo "⚠️  网关启动异常，请检查 /tmp/frugal-gateway.log"
fi

# 9. 创建 launchd 开机自启（macOS）
if [ "$(uname)" = "Darwin" ]; then
    LAUNCHD_PLIST="$HOME/Library/LaunchAgents/com.frugal-gateway.plist"
    LAUNCHD_SCRIPT="$SKILL_DIR/scripts/frugal-gateway-daemon.sh"

    cat > "$LAUNCHD_SCRIPT" << 'SCRIPT'
#!/bin/bash
sleep 5
exec python3 "$HOME/.openclaw/skills/frugal-coder/scripts/grok2api-fix-proxy.py" --port 4010
SCRIPT
    chmod +x "$LAUNCHD_SCRIPT"

    cat > "$LAUNCHD_PLIST" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 4.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.frugal-gateway</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$LAUNCHD_SCRIPT</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/frugal-gateway.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/frugal-gateway.log</string>
</dict>
</plist>
PLIST

    launchctl load "$LAUNCHD_PLIST" 2>/dev/null || true
    echo "✅ macOS launchd 开机自启已配置"
fi

echo ""
echo "========================"
echo "✅ 安装完成！"
echo ""
echo "下一步："
echo "1. 重启 OpenClaw（让 skill + 路由规则生效）：openclaw restart"
echo "2. 编辑 $SKILL_DIR/config.yaml 配置你的 API"
echo "3. 打开新对话测试：'写一个回文检测函数'"
echo ""
echo "管理命令："
echo "  查看网关日志：tail -f /tmp/frugal-gateway.log"
echo "  重启网关：launchctl load $LAUNCHD_PLIST"
echo "  卸载：launchctl unload $LAUNCHD_PLIST && rm -rf $SKILL_DIR"
