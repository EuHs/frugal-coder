#!/usr/bin/env python3
"""
thinking-classifier.py - Grok 思考引擎

每次用户消息都经过这里，Grok 输出完整的任务分解和执行指令。
主模型只执行，不思考。
"""

import json
import os
import sys
import urllib.request

PORT = os.environ.get("FRUGAL_PORT", "4010")
MODEL = os.environ.get("FRUGAL_MODEL", "YOUR_MODEL")

SYSTEM_PROMPT = """你是一个任务执行协调员（Coordinator）。

你的职责：
1. 分析用户请求
2. 判断任务类型
3. 分解为具体执行步骤
4. 指定每个步骤由谁执行：
   - 你自己（Grok）→ 直接执行，给出结果
   - 主模型（Main）→ 给出精确的执行指令

可用的执行工具（只有主模型能用，你不能直接调用）：
- exec: 执行 shell 命令
- read: 读取文件
- write: 写文件
- git: git 操作

你的输出格式（严格 JSON）：
{
  "thinking": "你的分析思路",
  "steps": [
    {
      "who": "grok|main",
      "action": "具体动作描述",
      "command": "如果是 main，给出精确命令",
      "tool": "exec|read|write|aider|frugal-ask|null",
      "files": ["涉及的文件路径"]
    }
  ],
  "summary": "一句话总结"
}

任务分类：
- CODE_WRITE: 写/编辑代码 → grok 通过 Aider 执行
- TEXT_QNA: 问答/翻译/总结 → grok 直接回答
- DESIGN: 架构/设计 → grok 直接回答
- SYSTEM_OP: 系统操作 → main 执行
- MIXED: 混合任务 → grok 出方案，main 执行具体步骤

注意：
- 你不能直接执行命令，只能规划
- 主模型不自己做决定，只执行你给出的指令
- 如果任务涉及文件读写，先让主模型读取，再交给你分析
"""


def think(prompt: str) -> dict:
    url = f"http://127.0.0.1:{PORT}/v1/chat/completions"
    payload = json.dumps({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ],
        "stream": False,
        "max_tokens": 2000,
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"].strip()
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
        return {"error": str(e), "steps": [], "thinking": "error"}


if __name__ == "__main__":
    if len(sys.argv) > 1:
        prompt = " ".join(sys.argv[1:])
    elif not sys.stdin.isatty():
        prompt = sys.stdin.read().strip()
    else:
        print(__doc__, file=sys.stderr)
        sys.exit(1)

    result = think(prompt)
    print(json.dumps(result, ensure_ascii=False, indent=2))
