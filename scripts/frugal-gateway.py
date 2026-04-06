#!/usr/bin/env python3
"""
frugal-gateway.py - Frugal Coder 统一网关

功能：
  1. SSE→JSON 修复（解决逆向 API 强制流式响应问题）
  2. 清理不支持参数（tools, tool_choice 等）
  3. 支持 OpenAI / Anthropic / 任意 OpenAI-compatible API
  4. 自动检测 SSE 响应并聚合

用法：
  python3 frugal-gateway.py [--config config.yaml] [--port 4010]

配置：
  首次运行会生成 config.yaml，编辑后重启即可。
  支持环境变量覆盖：
    FRUGAL_API_BASE   - 便宜模型的 API base URL
    FRUGAL_API_KEY    - API key
    FRUGAL_MODEL      - 模型 ID
    FRUGAL_PORT       - 网关端口
"""

import argparse
import json
import os
import ssl
import sys
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.request import Request, urlopen
from urllib.error import HTTPError

# ── 默认配置 ──────────────────────────────────────────
DEFAULTS = {
    "provider": {
        "api_base": "https://YOUR_UPSTREAM_API/v1",
        "api_key": "your-api-key-here",
        "model": "YOUR_MODEL",
        "fix_sse": True,          # 是否修复强制 SSE
        "strip_tools": True,      # 是否清理 tools 参数
        "timeout": 180,
    },
    "gateway": {
        "host": "127.0.0.1",
        "port": 4010,
    },
}

_cfg = {}


def load_config(path: str) -> dict:
    """加载配置，环境变量优先"""
    # 从文件加载
    file_cfg = {}
    if os.path.exists(path):
        try:
            import yaml
            with open(path) as f:
                file_cfg = yaml.safe_load(f) or {}
        except ImportError:
            # yaml 不可用，用 JSON fallback
            try:
                with open(path.replace(".yaml", ".json")) as f:
                    file_cfg = json.load(f)
            except FileNotFoundError:
                pass
        except Exception:
            pass

    # 合并默认值
    provider = {**DEFAULTS["provider"], **file_cfg.get("provider", {})}
    gateway = {**DEFAULTS["gateway"], **file_cfg.get("gateway", {})}

    # 环境变量覆盖（最高优先级）
    if os.environ.get("FRUGAL_API_BASE"):
        provider["api_base"] = os.environ["FRUGAL_API_BASE"]
    if os.environ.get("FRUGAL_API_KEY"):
        provider["api_key"] = os.environ["FRUGAL_API_KEY"]
    if os.environ.get("FRUGAL_MODEL"):
        provider["model"] = os.environ["FRUGAL_MODEL"]
    if os.environ.get("FRUGAL_PORT"):
        gateway["port"] = int(os.environ["FRUGAL_PORT"])

    return {"provider": provider, "gateway": gateway}


def _ssl_ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def aggregate_sse(response_bytes: bytes) -> dict:
    """把 SSE 流式响应聚合成标准 chat completion JSON"""
    text = response_bytes.decode("utf-8", errors="replace")
    parts = []
    role = "assistant"
    model = ""
    usage = None
    finish_reason = None

    for line in text.split("\n"):
        line = line.strip()
        if not line.startswith("data: "):
            continue
        raw = line[6:]
        if raw == "[DONE]":
            break
        try:
            chunk = json.loads(raw)
        except json.JSONDecodeError:
            continue
        model = chunk.get("model", model)
        for ch in chunk.get("choices", []):
            delta = ch.get("delta", {})
            if "role" in delta:
                role = delta["role"]
            if delta.get("content"):
                parts.append(delta["content"])
            if ch.get("finish_reason"):
                finish_reason = ch["finish_reason"]
        if "usage" in chunk:
            usage = chunk["usage"]

    return {
        "id": "frugal-gateway",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": role, "content": "".join(parts)},
            "finish_reason": finish_reason or "stop",
        }],
        "usage": usage or {},
    }


class Handler(BaseHTTPRequestHandler):
    """网关 handler"""

    def _upstream_url(self):
        path = self.path
        if path.startswith("/v1"):
            path = path[3:]
        return _cfg["provider"]["api_base"].rstrip("/") + path

    def _headers(self):
        return {
            "Authorization": f"Bearer {_cfg['provider']['api_key']}",
            "Content-Type": "application/json",
        }

    def do_GET(self):
        url = self._upstream_url()
        req = Request(url, headers={"Authorization": f"Bearer {_cfg['provider']['api_key']}"})
        try:
            with urlopen(req, timeout=30, context=_ssl_ctx()) as resp:
                body = resp.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
        except HTTPError as e:
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(e.read())
        except Exception as e:
            self._send_error(502, str(e))

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self._send_error(400, "invalid JSON")
            return

        # 清理不支持参数
        if _cfg["provider"].get("strip_tools", True):
            for key in ["tools", "tool_choice", "response_format",
                        "parallel_tool_calls", "stream_options"]:
                payload.pop(key, None)

        # 关闭流式（某些逆向 API 会忽略）
        payload["stream"] = False

        # 如果 payload 里的 model 是空或默认的，替换为配置的模型
        if not payload.get("model") or payload.get("model") in ("gpt-4", "gpt-3.5-turbo"):
            payload["model"] = _cfg["provider"]["model"]

        url = self._upstream_url()
        timeout = _cfg["provider"].get("timeout", 180)
        req = Request(url, data=json.dumps(payload).encode("utf-8"), headers=self._headers())

        try:
            with urlopen(req, timeout=timeout, context=_ssl_ctx()) as resp:
                resp_body = resp.read()
                ct = resp.headers.get("Content-Type", "")

                # 已经是标准 JSON
                if "text/event-stream" not in ct:
                    try:
                        data = json.loads(resp_body)
                        if "choices" in data:
                            self._send_json(data)
                            return
                    except json.JSONDecodeError:
                        pass

                # SSE → JSON 聚合
                if _cfg["provider"].get("fix_sse", True):
                    result = aggregate_sse(resp_body)
                    self._send_json(result)
                else:
                    self._send_error(502, "Unexpected SSE response and fix_sse is disabled")

        except HTTPError as e:
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(e.read())
        except Exception as e:
            self._send_error(502, str(e))

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def _send_json(self, data):
        body = json.dumps(data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, code, msg):
        body = json.dumps({"error": msg}).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


def write_default_config(path: str):
    """生成默认配置文件"""
    try:
        import yaml
        with open(path, "w") as f:
            yaml.dump(DEFAULTS, f, default_flow_style=False, allow_unicode=True)
    except ImportError:
        with open(path.replace(".yaml", ".json"), "w") as f:
            json.dump(DEFAULTS, f, indent=2, ensure_ascii=False)


def main():
    p = argparse.ArgumentParser(description="Frugal Coder Gateway - 统一 API 网关")
    p.add_argument("--config", type=str, default=None, help="配置文件路径")
    p.add_argument("--port", type=int, default=None, help="覆盖端口")
    p.add_argument("--api-base", type=str, default=None, help="覆盖 API base URL")
    p.add_argument("--api-key", type=str, default=None, help="覆盖 API key")
    p.add_argument("--model", type=str, default=None, help="覆盖模型 ID")
    args = p.parse_args()

    # 定位配置文件
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = args.config or os.path.join(script_dir, "..", "config.yaml")

    # 首次运行生成默认配置
    if not os.path.exists(config_path) and not os.path.exists(config_path.replace(".yaml", ".json")):
        write_default_config(config_path)
        print(f"📝 已生成默认配置: {config_path}")
        print(f"   请编辑后重新启动网关")

    # 加载配置
    global _cfg
    _cfg = load_config(config_path)

    # 命令行参数覆盖
    if args.port:
        _cfg["gateway"]["port"] = args.port
    if args.api_base:
        _cfg["provider"]["api_base"] = args.api_base
    if args.api_key:
        _cfg["provider"]["api_key"] = args.api_key
    if args.model:
        _cfg["provider"]["model"] = args.model

    host = _cfg["gateway"]["host"]
    port = _cfg["gateway"]["port"]

    server = HTTPServer((host, port), Handler)
    print(f"🛡️  Frugal Coder Gateway")
    print(f"   监听:    http://{host}:{port}")
    print(f"   上游:    {_cfg['provider']['api_base']}")
    print(f"   模型:    {_cfg['provider']['model']}")
    print(f"   SSE修复: {_cfg['provider'].get('fix_sse', True)}")
    print(f"   清理tools: {_cfg['provider'].get('strip_tools', True)}")
    print()
    print(f"Aider 用法:")
    print(f"  OPENAI_API_BASE=http://{host}:{port}/v1 \\")
    print(f"  OPENAI_API_KEY=any \\")
    print(f"  aider --model openai/{_cfg['provider']['model']} --no-stream")
    print()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑 已停止")
        server.server_close()


if __name__ == "__main__":
    main()
