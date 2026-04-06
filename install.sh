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

# 6. 配置 OpenClaw 加载 skill
echo "⚙️  配置 OpenClaw..."
CONFIG="$OPENCLAW_DIR/openclaw.json"
if [ -f "$CONFIG" ]; then
    # 检查是否已有 extraDirs
    if grep -q '"extraDirs"' "$CONFIG"; then
        # 已存在 extraDirs，确保包含 skills
        echo "  (openclaw.json extraDirs 已存在，跳过)"
    else
        # 需要添加 extraDirs
        python3 -c "
import json, sys

config_path = '$CONFIG'
with open(config_path, 'r') as f:
    config = json.load(f)

# 添加 extraDirs
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
    -d '{"model":"grok-4.1-fast","messages":[{"role":"user","content":"ok"}],"max_tokens":5}' \
    | grep -q "ok"; then
    echo "✅ 网关运行正常（端口 4010）"
else
    echo "⚠️  网关启动异常，请检查 /tmp/frugal-gateway.log"
fi

# 9. 创建 launchd 开机自启（macOS）
if [ "$(uname)" = "Darwin" ]; then
    LAUNCHD_PLIST="$HOME/Library/LaunchAgents/com.frugal-gateway.plist"
    LAUNCHD_SCRIPT="$SKILL_DIR/scripts/frugal-gateway-daemon.sh"

    # 创建 daemon 启动脚本
    cat > "$LAUNCHD_SCRIPT" << 'SCRIPT'
#!/bin/bash
sleep 5
exec python3 "$HOME/.openclaw/skills/frugal-coder/scripts/grok2api-fix-proxy.py" --port 4010
SCRIPT
    chmod +x "$LAUNCHD_SCRIPT"

    # 创建 launchd plist
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
echo "1. 重启 OpenClaw（让 skill 加载）"
echo "2. 编辑 $SKILL_DIR/config.yaml 配置你的 API"
echo "3. 打开新对话测试：'写一个回文检测函数'"
echo ""
echo "管理命令："
echo "  查看网关日志：tail -f /tmp/frugal-gateway.log"
echo "  重启网关：launchctl load $LAUNCHD_PLIST"
echo "  卸载：launchctl unload $LAUNCHD_PLIST && rm -rf $SKILL_DIR"
