#!/usr/bin/env python3
"""
frugal-ask.py - 通过 frugal-gateway 向便宜模型提问

用法:
  python3 frugal-ask.py "你的问题"
  echo "问题" | python3 frugal-ask.py

环境变量:
  FRUGAL_PORT  - 网关端口（默认 4010）
  FRUGAL_MODEL - 模型 ID（可选，使用网关默认）
"""

import json
import os
import sys
import urllib.request

PORT = os.environ.get("FRUGAL_PORT", "4010")
MODEL = os.environ.get("FRUGAL_MODEL", "any")


def ask(prompt: str) -> str:
    url = f"http://127.0.0.1:{PORT}/v1/chat/completions"
    payload = json.dumps({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "简洁有用。直接回答，不要废话。"},
            {"role": "user", "content": prompt}
        ],
        "stream": False,
        "max_tokens": 8192,
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if "choices" in data and data["choices"]:
                return data["choices"][0]["message"]["content"]
            return f"[ERROR] Unexpected response: {json.dumps(data)[:200]}"
    except urllib.error.HTTPError as e:
        return f"[ERROR] HTTP {e.code}: {e.read().decode()[:200]}"
    except Exception as e:
        return f"[ERROR] {e}"


if __name__ == "__main__":
    if len(sys.argv) > 1:
        prompt = " ".join(sys.argv[1:])
    elif not sys.stdin.isatty():
        prompt = sys.stdin.read().strip()
    else:
        print(__doc__, file=sys.stderr)
        sys.exit(1)

    print(ask(prompt))
