#!/usr/bin/env python3
"""
smart-router.py — 智能路由网关

拦截 OpenClaw 发给主模型的所有请求，先让便宜模型判断：
- 简单对话/问答/翻译/解释 → 便宜模型直接回（免费）
- 需要工具调用 → 转发给真实主模型（花钱，但只有必要时）

架构:
  OpenClaw → localhost:4020 (smart-router)
                  ↓
           便宜模型分类（~200 tokens）
                  ↓
      ┌───────────┴───────────┐
      ↓                       ↓
  便宜模型直接回          转发给真实主模型
  （免费，~70%请求）     （花钱，~30%请求）

用法:
  python3 smart-router.py [--port 4020]

环境变量:
  SMART_ROUTER_PORT    - 路由器端口（默认 4020）
  CHEAP_MODEL_PORT     - 便宜模型端口（默认 4010）
  CHEAP_MODEL_NAME     - 便宜模型名称（通过环境变量指定）
  MAIN_MODEL_URL       - 真实主模型 URL
  MAIN_MODEL_KEY       - 真实主模型 API key
  MAIN_MODEL_NAME      - 真实主模型名称
"""

import json
import os
import sys
import time
import uuid
import argparse
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

# ─── 配置 ──────────────────────────────────────────────

ROUTER_PORT = int(os.environ.get("SMART_ROUTER_PORT", "4020"))
CHEAP_PORT = os.environ.get("CHEAP_MODEL_PORT", "4010")
CHEAP_MODEL = os.environ.get("CHEAP_MODEL_NAME", "YOUR_MODEL")
CHEAP_URL = f"http://127.0.0.1:{CHEAP_PORT}/v1/chat/completions"

# 真实主模型（OpenAI-compatible）
MAIN_URL = os.environ.get("MAIN_MODEL_URL", "")
MAIN_KEY = os.environ.get("MAIN_MODEL_KEY", "")
MAIN_MODEL = os.environ.get("MAIN_MODEL_NAME", "")

# 统计
stats = {
    "total_requests": 0,
    "cheap_handled": 0,
    "react_handled": 0,
    "main_handled": 0,
    "errors": 0,
    "started_at": datetime.now().isoformat(),
}

# ─── 工具函数 ──────────────────────────────────────────


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def call_cheap_api(messages, max_tokens=2048, stream=False, retries=3):
    """调用便宜模型（带重试，应对上游间歇性 403/502）"""
    import urllib.request
    import urllib.error
    import time

    payload = json.dumps({
        "model": CHEAP_MODEL,
        "messages": messages,
        "stream": stream,
        "max_tokens": max_tokens,
    }).encode("utf-8")

    req = urllib.request.Request(
        CHEAP_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode() if e.fp else ""
            if e.code in (502, 503, 403) and attempt < retries - 1:
                log(f"  ⚠️ 便宜模型 尝试 {attempt+1} 失败 ({e.code})，重试...")
                time.sleep(1)
                continue
            log(f"  ❌ 便宜模型 HTTP {e.code}: {body[:100]}")
            return None
        except Exception as e:
            log(f"  ❌ 便宜模型调用失败: {e}")
            return None
    return None


def call_main_api(payload):
    """转发给真实主模型"""
    import urllib.request
    import urllib.error

    if not MAIN_URL:
        return None

    headers = {"Content-Type": "application/json"}
    if MAIN_KEY:
        headers["Authorization"] = f"Bearer {MAIN_KEY}"

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(MAIN_URL, data=data, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        log(f"  ❌ 主模型调用失败: {e}")
        return None


def extract_last_user_message(messages):
    """提取最后一条用户消息"""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, list):
                # 多模态消息
                text_parts = []
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text_parts.append(part.get("text", ""))
                return " ".join(text_parts)
            return content
    return ""


def classify_request(user_msg, has_tools, system_hint=""):
    """三级分类：SIMPLE / COMPLEX / NEEDS_TOOLS"""
    classify_prompt = f"""你是请求分类器。把用户请求分为三类：

用户消息: {user_msg[:500]}
系统提示摘要: {system_hint[:200]}

=== 分类规则 ===

SIMPLE（简单认知，便宜模型直接回）:
- 闲聊、问候、简单确认
- 问答、解释、翻译、总结
- 代码分析/审查/设计建议
- 代码编写（不需要执行）
- 简单比较、推荐

COMPLEX（复杂认知，需要 ReAct 多步编排）:
- 深度分析/调研任务（如"分析某支股票""调查某技术方案"）
- 需要搜索+分析+综合的多步推理
- 需要多轮数据收集后才能回答的问题
- 比较研究（需要搜索多个对象后对比）
- 报告生成（需要多源数据综合）

NEEDS_TOOLS（需要 OpenClaw 系统工具，必须主模型）:
- 需要执行命令/脚本（exec）
- 需要读写文件（read/write）
- 需要浏览器操作（browser）
- 需要发送消息（message）
- 需要定时任务（cron）
- 心跳检查（HEARTBEAT）
- 需要 OpenClaw 配置/网关操作
- 需要操作 session/subagent

只回答一个词: SIMPLE 或 COMPLEX 或 NEEDS_TOOLS"""

    result = call_cheap_api([
        {"role": "system", "content": "你是请求分类器。只回答 SIMPLE 或 COMPLEX 或 NEEDS_TOOLS。"},
        {"role": "user", "content": classify_prompt},
    ], max_tokens=10)

    if result and "choices" in result:
        answer = result["choices"][0]["message"]["content"].strip().upper()
        if "COMPLEX" in answer and "NEEDS" not in answer:
            return "COMPLEX"
        if "NEEDS_TOOLS" in answer:
            return "NEEDS_TOOLS"
    return "SIMPLE"


def run_react_orchestrator(task: str, max_iter: int = 6) -> dict | None:
    """调用 ReAct 编排器处理复杂认知任务（全程便宜模型，0 主模型 token）"""
    try:
        react_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "react-orchestrate.py")
        import importlib.util
        spec = importlib.util.spec_from_file_location("react_orchestrate", react_path)
        mod = importlib.util.module_from_spec(spec)
        # 确保编排器使用便宜模型（必须在 exec_module 之前设置）
        os.environ["FRUGAL_MODEL"] = CHEAP_MODEL
        os.environ["FRUGAL_PORT"] = CHEAP_PORT
        spec.loader.exec_module(mod)

        log(f"  🧩 ReAct 启动: {task[:60]}...")

        registry = mod.default_registry()
        orch = mod.ReActOrchestrator(
            registry=registry,
            model=CHEAP_MODEL,
            max_iterations=max_iter,
            verbose=False,
        )
        result = orch.run(task)

        answer = result.final_answer
        iterations = result.iterations
        log(f"  🧩 ReAct 完成: {iterations} 步, {len(answer)} 字")

        return make_openai_response(answer, model=f"react-{CHEAP_MODEL}")
    except Exception as e:
        log(f"  🧩 ReAct 异常: {e}")
        return None


def cheap_model_respond(messages, max_tokens=4096):
    """让便宜模型直接回答（对过长消息做截断）"""
    clean_messages = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")

        # 跳过工具调用结果
        if role == "tool":
            continue
        # 跳过包含 tool_calls 的助手消息
        if role == "assistant" and msg.get("tool_calls"):
            continue

        # 内容截断（防止上游 API 大小限制）
        if isinstance(content, str) and len(content) > 800:
            content = content[:800] + "...[已截断]"

        clean_messages.append({"role": role, "content": content})

    if not clean_messages:
        clean_messages = messages  # fallback

    result = call_cheap_api(clean_messages, max_tokens=max_tokens)
    return result


def make_openai_response(content, model="smart-router"):
    """构造 OpenAI 格式响应"""
    return {
        "id": f"sr-{uuid.uuid4().hex[:8]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": content,
            },
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }


# ─── HTTP Handler ──────────────────────────────────────


class SmartRouterHandler(BaseHTTPRequestHandler):
    """智能路由 HTTP 处理器"""

    def log_message(self, format, *args):
        pass  # 静默默认日志

    def _send_json(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length:
            return json.loads(self.rfile.read(length))
        return {}

    def do_GET(self):
        """处理 GET 请求（健康检查、模型列表等）"""
        if self.path == "/v1/models":
            models = [
                {"id": CHEAP_MODEL, "object": "model", "owned_by": "cheap"},
                {"id": MAIN_MODEL or "main-model", "object": "model", "owned_by": "main"},
            ]
            self._send_json(200, {"object": "list", "data": models})
        elif self.path == "/health":
            self._send_json(200, {
                "status": "ok",
                "stats": stats,
                "cheap_model": CHEAP_MODEL,
                "main_model": MAIN_MODEL or "(not configured)",
            })
        elif self.path == "/stats":
            self._send_json(200, stats)
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        """处理 POST 请求（核心路由逻辑）"""
        if self.path != "/v1/chat/completions":
            self._send_json(404, {"error": "not found"})
            return

        stats["total_requests"] += 1
        body = self._read_body()

        messages = body.get("messages", [])
        tools = body.get("tools", [])
        max_tokens = body.get("max_tokens", 4096)
        stream = body.get("stream", False)

        # 提取用户消息和系统提示
        user_msg = extract_last_user_message(messages)
        system_hint = ""
        for m in messages:
            if m.get("role") == "system":
                system_hint += m.get("content", "")[:300] + " "

        has_tools = len(tools) > 0

        # === 心跳和特殊消息直接转发 ===
        if "HEARTBEAT" in user_msg or "heartbeat" in user_msg:
            log(f"💓 心跳 → 主模型")
            stats["main_handled"] += 1
            result = call_main_api(body)
            if result:
                self._send_json(200, result)
            else:
                self._send_json(502, {"error": "main model unavailable"})
            return

        # === 分类 ===
        log(f"📨 分类: {user_msg[:80]}...")
        classification = classify_request(user_msg, has_tools, system_hint)

        if classification == "COMPLEX":
            # === ReAct 编排器处理 ===
            log(f"  🧩 → ReAct 编排器（复杂认知任务）")
            stats["react_handled"] += 1

            react_result = run_react_orchestrator(user_msg)
            if react_result:
                self._send_json(200, react_result)
            else:
                # ReAct 失败，升级到主模型
                log(f"  ⚠️ ReAct 失败，升级到便宜模型")
                stats["react_handled"] -= 1
                stats["main_handled"] += 1
                result = cheap_model_respond(messages, max_tokens)
                if result:
                    self._send_json(200, result)
                else:
                    self._send_json(502, {"error": "all models unavailable"})

        elif classification == "NEEDS_TOOLS":
            # 需要系统工具 → 用便宜模型处理
            # 策略：把系统提示+工具描述合并到用户消息里发单消息，绕过上游多消息限制
            log(f"  🔧 → 便宜模型（工具类请求）")
            stats["cheap_handled"] += 1

            # 提取系统提示和工具描述
            tool_desc = ""
            if tools:
                for t in tools:
                    fn = t.get("function", {})
                    tool_desc += f"- {fn.get('name','')}: {fn.get('description','')}\n"

            # 提取真实用户消息
            user_content = extract_last_user_message(messages)

            # 构造单消息请求（绕过上游多消息 403 限制）
            condensed = []
            if tool_desc:
                condensed.append({
                    "role": "user",
                    "content": f"[工具可用: \n{tool_desc}]\n\n用户请求: {user_content}"
                })
            else:
                condensed.append({"role": "user", "content": user_content})

            result = call_cheap_api(condensed, max_tokens=max_tokens)
            if result:
                self._send_json(200, result)
            else:
                log(f"  ⚠️ 便宜模型失败，尝试主模型")
                stats["cheap_handled"] -= 1
                stats["main_handled"] += 1
                fallback = call_main_api(body)
                if fallback:
                    self._send_json(200, fallback)
                else:
                    self._send_json(502, {"error": "all models unavailable"})
        else:
            # 便宜模型直接回答
            log(f"  ✅ → 便宜模型（简单请求）")
            stats["cheap_handled"] += 1

            result = cheap_model_respond(messages, max_tokens)
            if result:
                self._send_json(200, result)
            else:
                log(f"  ⚠️ 便宜模型失败，fallback 到主模型")
                stats["cheap_handled"] -= 1
                stats["main_handled"] += 1
                fallback = call_main_api(body)
                if fallback:
                    self._send_json(200, fallback)
                else:
                    self._send_json(502, {"error": "all models unavailable"})

    def do_OPTIONS(self):
        """CORS"""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()


def run_server(port):
    server = HTTPServer(("127.0.0.1", port), SmartRouterHandler)
    log(f"🚀 Smart Router 启动于 http://127.0.0.1:{port}")
    log(f"   便宜模型: {CHEAP_MODEL} (port {CHEAP_PORT})")
    log(f"   主模型: {MAIN_MODEL or '(未配置)'} ({MAIN_URL or '未配置'})")
    log(f"   统计: http://127.0.0.1:{port}/stats")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("\n🛑 Smart Router 关闭")
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Smart Router - 智能路由网关")
    parser.add_argument("--port", type=int, default=ROUTER_PORT)
    parser.add_argument("--main-url", default=MAIN_URL, help="真实主模型 API URL")
    parser.add_argument("--main-key", default=MAIN_KEY, help="真实主模型 API key")
    parser.add_argument("--main-model", default=MAIN_MODEL, help="真实主模型名称")
    parser.add_argument("--cheap-model", default=CHEAP_MODEL, help="便宜模型名称")
    args = parser.parse_args()

    # 更新配置
    MAIN_URL = args.main_url
    MAIN_KEY = args.main_key
    MAIN_MODEL = args.main_model
    CHEAP_MODEL = args.cheap_model

    run_server(args.port)
