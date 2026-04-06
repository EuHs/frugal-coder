#!/usr/bin/env python3
"""
task-classifier.py - 自动任务分类器

自动分析用户请求 → 匹配路由表 → 返回任务类型和处理建议
供 OpenClaw 主模型调用，实现全自动路由。
"""

import json
import os
import sys
import urllib.request

PORT = os.environ.get("FRUGAL_PORT", "4010")
MODEL = os.environ.get("FRUGAL_MODEL", "YOUR_MODEL")


ROUTING_TABLE = """
你是一个任务分类器。根据用户请求，从以下类别中选择最匹配的：

类别定义：
1. CODE_WRITE - 写代码/编辑代码/重构代码（任何语言的代码生成、修改）
2. CODE_REVIEW - 代码审查/代码解释/代码调试/错误分析
3. DESIGN - 架构设计/技术选型/系统设计/流程设计
4. TEXT_QNA - 问答/翻译/总结/解释概念/写文档
5. BRAINSTORM - 头脑风暴/讨论方案/分析问题思路
6. SYSTEM_OP - 系统操作/执行命令/文件管理/git操作/运行测试
7. SIMPLE_CHAT - 简单闲聊/确认/问候
8. COMPLEX_CHAT - 深度讨论/需要分析的观点/技术趋势讨论

输出格式（只输出 JSON，不要其他内容）：
{
  "category": "类别名",
  "use_cheap_model": true或false,
  "need_main_model": true或false,
  "sub_tasks": ["步骤1", "步骤2"],
  "summary": "一句话总结任务"
}

路由规则：
- CODE_WRITE → cheap model (Aider)
- CODE_REVIEW → cheap model (frugal-ask)
- DESIGN → cheap model (frugal-ask)
- TEXT_QNA → cheap model (frugal-ask)
- BRAINSTORM → cheap model (frugal-ask)
- SYSTEM_OP → main model (exec)
- SIMPLE_CHAT → main model (直接回复)
- COMPLEX_CHAT → cheap model 生成初稿 → main model 润色
"""


def classify(prompt: str) -> dict:
    url = f"http://127.0.0.1:{PORT}/v1/chat/completions"
    payload = json.dumps({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": ROUTING_TABLE},
            {"role": "user", "content": prompt}
        ],
        "stream": False,
        "max_tokens": 500,
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"].strip()
            # 提取 JSON
            if "```json" in content:
                start = content.find("```json") + 7
                end = content.find("```", start)
                content = content[start:end].strip()
            elif "```" in content:
                start = content.find("```") + 3
                end = content.find("```", start)
                content = content[start:end].strip()
            return json.loads(content)
    except Exception as e:
        return {"error": str(e), "category": "UNKNOWN"}


if __name__ == "__main__":
    if len(sys.argv) > 1:
        prompt = " ".join(sys.argv[1:])
    elif not sys.stdin.isatty():
        prompt = sys.stdin.read().strip()
    else:
        print(__doc__, file=sys.stderr)
        sys.exit(1)

    result = classify(prompt)
    print(json.dumps(result, ensure_ascii=False, indent=2))
