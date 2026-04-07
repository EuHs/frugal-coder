#!/usr/bin/env python3
"""
sse-fix-proxy.py
中间代理：把上游 API 强制返回的 SSE 流式响应聚合成标准 OpenAI 非流式 JSON 响应。
兼容所有只支持流式响应的上游 API（如 grok2api、Ollama 等）。

用法:
  python3 sse-fix-proxy.py [--port 4010]

Aider 用法:
  OPENAI_API_BASE=http://127.0.0.1:4010/v1 OPENAI_API_KEY=YOUR_API_KEY \
    aider --model openai/YOUR_MODEL --no-show-model-warnings
"""

import argparse
import json
import ssl
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.request import Request, urlopen
from urllib.error import HTTPError

# defaults, overridden by CLI args
_config = {
    "upstream": "https://www.YOUR_UPSTREAM_API/v1",
    "api_key": "YOUR_API_KEY",
}


def _ssl_ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def aggregate_sse(response_bytes: bytes) -> dict:
    """把 SSE 流式响应聚合成完整 chat completion JSON"""
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
        "id": "sse-fix-proxy",
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
    """代理 handler：GET 透传，POST 聚合 SSE"""

    def _upstream_url(self):
        # upstream already includes /v1, strip it from request path
        path = self.path
        if path.startswith("/v1"):
            path = path[3:]  # strip /v1 prefix
        return _config["upstream"] + path

    def _proxy_get(self):
        url = self._upstream_url()
        req = Request(url, headers={"Authorization": f"Bearer {_config['api_key']}"})
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

    def _proxy_post(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'{"error":"invalid JSON"}')
            return

        # 清理上游不支持的参数
        for key in ["tools", "tool_choice", "response_format", "parallel_tool_calls", "stream_options"]:
            payload.pop(key, None)

        # 尝试请求非流式（部分上游会忽略，但以防万一）
        payload["stream"] = False

        url = self._upstream_url()
        req = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {_config['api_key']}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(req, timeout=180, context=_ssl_ctx()) as resp:
                resp_body = resp.read()
                ct = resp.headers.get("Content-Type", "")

                # 如果已经是标准 JSON
                if "text/event-stream" not in ct:
                    try:
                        data = json.loads(resp_body)
                        if "choices" in data:
                            self.send_response(200)
                            self.send_header("Content-Type", "application/json")
                            self.end_headers()
                            self.wfile.write(json.dumps(data).encode())
                            return
                    except json.JSONDecodeError:
                        pass

                # SSE → JSON
                result = aggregate_sse(resp_body)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(result).encode())

        except HTTPError as e:
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(e.read())
        except Exception as e:
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def do_GET(self):
        self._proxy_get()

    def do_POST(self):
        self._proxy_post()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def log_message(self, fmt, *args):
        pass  # 静默


def main():
    p = argparse.ArgumentParser(description="SSE→JSON 修复代理（兼容所有流式上游）")
    p.add_argument("--port", type=int, default=4010)
    p.add_argument("--upstream", type=str, default="https://www.YOUR_UPSTREAM_API/v1")
    p.add_argument("--api-key", type=str, default="YOUR_API_KEY")
    args = p.parse_args()

    _config["upstream"] = args.upstream.rstrip("/")
    _config["api_key"] = args.api_key

    server = HTTPServer(("127.0.0.1", args.port), Handler)
    print(f"🔧 sse-fix-proxy 启动")
    print(f"   监听: http://127.0.0.1:{args.port}")
    print(f"   上游: {_config['upstream']}")
    print(f"   功能: SSE 流式 → 标准 JSON")
    print(f"   Aider: OPENAI_API_BASE=http://127.0.0.1:{args.port}/v1 OPENAI_API_KEY={_config['api_key']} aider --model openai/YOUR_MODEL")
    print()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑 已停止")
        server.server_close()


if __name__ == "__main__":
    main()
