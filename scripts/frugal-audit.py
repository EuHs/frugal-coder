#!/usr/bin/env python3
"""
frugal-audit.py - 每天分析前一天聊天记录，找出 frugal-coder 本应介入但没介入的任务
每天 08:00 Asia/Shanghai 运行，自动更新路由表

Usage:
  python3 frugal-audit.py [--date YYYY-MM-DD]
"""

import json
import os
import sys
import re
from datetime import datetime, timedelta
from pathlib import Path

SESSION_DIR = Path.home() / ".openclaw/agents/main/sessions"
ROUTING_TABLE = Path.home() / ".openclaw/workspace/frugal-coder/routing-table.md"
SOUL_PATH = Path.home() / ".openclaw/workspace/SOUL.md"

# 认知任务关键词（应该委托给 cheap model）
COGNITIVE_PATTERNS = [
    (r"写(代码|程序|函数|类|脚本)", "CODE_WRITE"),
    (r"实现(一个|个)?", "CODE_WRITE"),
    (r"(python|js|ts|java|c\+\+|go|rust|shell|bash)代码", "CODE_WRITE"),
    (r"帮我生成", "CODE_WRITE"),
    (r"怎么(写|实现|做|解决)", "CODE_WRITE"),
    (r"解释(这个|下|一下|代码|概念|原理)", "EXPLAIN"),
    (r"为什么|原因|原理", "EXPLAIN"),
    (r"什么意思", "EXPLAIN"),
    (r"翻译成", "TRANSLATE"),
    (r"翻译(成|为)", "TRANSLATE"),
    (r"总结一下|概括|摘要", "SUMMARY"),
    (r"给我(一个)?建议|推荐", "SUGGESTION"),
    (r"设计(一个|个)?", "DESIGN"),
    (r"架构|framework|技术方案", "DESIGN"),
    (r"(代码)?审查|review|看看(代码|这个)", "CODE_REVIEW"),
    (r"调试|debug|报错|出错了", "DEBUG"),
    (r"优化(一下)?|性能|效率", "OPTIMIZE"),
    (r"(代码)?有.*问题|修复", "FIX"),
    (r"请分析|分析一下", "ANALYSIS"),
    (r"对比|比较.*和|差异", "COMPARE"),
]

# 主模型处理了但本应委托的（漏网模式）
MAIN_MODEL_LEAK_PATTERNS = [
    r"```python",
    r"```javascript",
    r"```typescript",
    r"```java",
    r"```go",
    r"```rust",
    r"```shell",
    r"```bash",
    r"def\s+\w+\(",
    r"class\s+\w+",
    r"function\s+\w+\(",
    r"import\s+\w+",
    r"const\s+\w+\s*=",
    r"let\s+\w+\s*=",
    r"// 这里是",
    r"# 这里是",
    r"以下是.*代码",
    r"你可以这样写：",
    r"这样实现：",
]


def get_yesterday():
    if len(sys.argv) > 2 and sys.argv[1] == "--date":
        return sys.argv[2]
    return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")


def find_sessions_for_date(date_str):
    """找到指定日期的 session 文件"""
    sessions = []
    for f in SESSION_DIR.glob("*.jsonl"):
        try:
            with open(f) as fp:
                first = fp.readline()
                if first:
                    d = json.loads(first)
                    ts = d.get("timestamp", "")[:10]
                    if ts == date_str:
                        sessions.append(f)
        except:
            pass
    return sessions


def extract_messages(session_path, date_str):
    """从 session 提取用户消息和助手回复对"""
    messages = []
    current_user = None

    with open(session_path) as fp:
        for line in fp:
            try:
                d = json.loads(line)
            except:
                continue

            if d.get("type") != "message":
                continue

            msg = d.get("message", {})
            role = msg.get("role", "")
            content_parts = msg.get("content", [])

            text = ""
            for part in content_parts:
                if isinstance(part, dict) and part.get("type") == "text":
                    text += part.get("text", "")
            text = text.strip()

            if not text:
                continue

            if role == "user":
                # 跳过 metadata 前缀
                text = re.sub(r"Sender \(untrusted metadata\):.*?```", "", text, flags=re.DOTALL).strip()
                text = re.sub(r"^\[.*?\]\s*", "", text).strip()
                current_user = text
            elif role == "assistant" and current_user:
                messages.append((current_user, text))
                current_user = None

    return messages


def classify_task(text):
    """识别任务类型"""
    for pattern, label in COGNITIVE_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return label
    return None


def is_main_model_leak(user_msg, assistant_msg):
    """判断助手是否自己写了代码（本应委托给 Aider）"""
    if not any(re.search(p, assistant_msg, re.IGNORECASE) for p in MAIN_MODEL_LEAK_PATTERNS):
        return False

    # 检查是否调用了工具（spawn/exec/read/write）
    # 如果有 tool_calls 说明正确委托了
    # 这个检测需要看完整的 assistant 消息结构
    return True


def analyze_session(session_path, date_str):
    """分析单个 session"""
    messages = extract_messages(session_path, date_str)
    missed = []

    for user_msg, assistant_msg in messages:
        task_type = classify_task(user_msg)
        if task_type in ("CODE_WRITE", "CODE_REVIEW", "DEBUG", "OPTIMIZE", "FIX"):
            # 检查是否主模型自己处理了代码
            if any(re.search(p, assistant_msg, re.IGNORECASE) for p in MAIN_MODEL_LEAK_PATTERNS):
                missed.append({
                    "task_type": task_type,
                    "user_msg": user_msg[:200],
                    "snippet": assistant_msg[:300],
                })

    return missed


def generate_report(date_str, all_missed):
    """生成审计报告"""
    if not all_missed:
        return f"## Frugal Audit Report - {date_str}\n\n✅ 没有发现遗漏委托，所有认知任务都正确处理了。\n"

    report = f"""## Frugal Audit Report - {date_str}

### 📊 统计
- 总遗漏任务：{len(all_missed)}
- 类型分布："""

    type_counts = {}
    for m in all_missed:
        t = m["task_type"]
        type_counts[t] = type_counts.get(t, 0) + 1

    for t, c in sorted(type_counts.items(), key=lambda x: -x[1]):
        report += f"\n  - {t}: {c}"

    report += "\n\n### 🔍 遗漏详情\n"
    for i, m in enumerate(all_missed, 1):
        report += f"""
#### {i}. [{m['task_type']}] 
**用户消息:** {m['user_msg'][:150]}...
**主模型回复片段:** 
{m['snippet'][:200]}...
---
"""

    # 生成路由建议
    report += "\n### 💡 路由改进建议\n"
    for t in type_counts:
        if type_counts[t] >= 1:
            report += f"- **{t}**: 当用户发送 {t} 类型的请求时，优先通过 thinking-classifier 分类后再委托\n"

    return report


def update_routing_table(report, date_str):
    """追加报告到路由表"""
    ROUTING_TABLE.parent.mkdir(parents=True, exist_ok=True)

    existing = ""
    if ROUTING_TABLE.exists():
        existing = ROUTING_TABLE.read_text()

    # 如果已有当天的报告就跳过
    if f"## Audit - {date_str}" in existing:
        return "already_updated"

    updated = existing + f"\n\n{report}"
    ROUTING_TABLE.write_text(updated.strip() + "\n")
    return "updated"


def inject_into_soul(new_insights, date_str):
    """将有价值的洞见注入 SOUL.md"""
    if not new_insights:
        return

    soul = SOUL_PATH.read_text()

    # 找到 ## 路由规则 部分，在其下追加
    marker = "## 路由规则"
    insight_block = f"""

### {date_str} 审计改进
{new_insights}
"""

    if marker in soul:
        if insight_block.strip() not in soul:
            soul = soul.replace(marker, insight_block + "\n" + marker)
            SOUL_PATH.write_text(soul)
            return "injected"
    return "no_marker"


def main():
    date_str = get_yesterday()
    print(f"📅 分析日期: {date_str}")

    sessions = find_sessions_for_date(date_str)
    print(f"📂 找到 {len(sessions)} 个 session 文件")

    if not sessions:
        print("⚠️ 没有找到该日期的 session")
        sys.exit(0)

    all_missed = []
    for s in sessions:
        missed = analyze_session(s, date_str)
        all_missed.extend(missed)
        if missed:
            print(f"  {s.name}: {len(missed)} 个遗漏")

    report = generate_report(date_str, all_missed)
    print(f"\n{report}")

    # 更新路由表
    result = update_routing_table(report, date_str)
    if result == "updated":
        print(f"\n✅ 报告已写入 {ROUTING_TABLE}")
    elif result == "already_updated":
        print(f"\nℹ️ 今日报告已存在，跳过")

    # 注入 SOUL.md
    if all_missed:
        insights_lines = [f"- [{m['task_type']}] {m['user_msg'][:80]}..." for m in all_missed[:5]]
        insights = "\n".join(insights_lines)
        inj = inject_into_soul(insights, date_str)
        if inj == "injected":
            print(f"✅ 已注入洞见到 {SOUL_PATH}")


if __name__ == "__main__":
    main()
