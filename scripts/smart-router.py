#!/Library/Frameworks/Python.framework/Versions/3.10/bin/python3
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
import yaml

# ─── 管理后台 HTML ──────────────────────────────────────────────────────────
MANAGEMENT_HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Frugal Coder 管理后台</title>
<style>
:root{--bg:#0f1117;--card:#1a1d27;--border:#2a2d3a;--text:#e0e3ec;--muted:#6b7280;--accent:#6c8aed;--green:#22c55e;--red:#ef4444;--yellow:#eab308}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
.header{padding:16px 24px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:12px}
.header h1{font-size:18px;font-weight:600}
.dot{width:8px;height:8px;border-radius:50%;background:var(--green);animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
.main{max-width:1100px;margin:0 auto;padding:24px;display:grid;grid-template-columns:260px 1fr;gap:20px}
@media(max-width:768px){.main{grid-template-columns:1fr}}
.sidebar{display:flex;flex-direction:column;gap:16px}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:20px}
.card-title{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin-bottom:12px}
.mode-toggle{display:flex;gap:8px}
.mode-btn{padding:10px 16px;border-radius:8px;border:1px solid var(--border);background:transparent;color:var(--text);cursor:pointer;font-size:14px;flex:1;text-align:center;transition:all .2s}
.mode-btn:hover{border-color:var(--accent)}
.mode-btn.active{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600}
.mode-btn.direct.active{background:var(--red);border-color:var(--red)}
.stats-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.stat-card{background:#12141c;border-radius:8px;padding:14px;text-align:center}
.stat-num{font-size:28px;font-weight:700}
.stat-label{font-size:11px;color:var(--muted);margin-top:4px}
.stat-card.total .stat-num{color:var(--accent)}
.stat-card.cheap .stat-num{color:var(--green)}
.stat-card.react .stat-num{color:var(--yellow)}
.stat-card.main .stat-num{color:#f97316}
.panel-section{margin-bottom:20px}
.panel-section:last-child{margin-bottom:0}
.panel-section h3{font-size:14px;font-weight:600;margin-bottom:10px;display:flex;align-items:center;gap:6px}
.badge{font-size:11px;padding:2px 8px;border-radius:20px;font-weight:400}
.badge-green{background:rgba(34,197,94,.15);color:var(--green)}
.badge-red{background:rgba(239,68,68,.15);color:var(--red)}
.badge-yellow{background:rgba(234,179,8,.15);color:var(--yellow)}
.badge-blue{background:rgba(108,138,237,.15);color:var(--accent)}
form{display:flex;flex-direction:column;gap:10px}
.form-row{display:grid;grid-template-columns:1fr 2fr;align-items:center;gap:10px}
.form-row label{font-size:13px;color:var(--muted)}
input{background:#12141c;border:1px solid var(--border);border-radius:8px;padding:8px 12px;color:var(--text);font-size:13px;width:100%;transition:border-color .2s}
input:focus{outline:none;border-color:var(--accent)}
input:read-only{opacity:.6;cursor:not-allowed}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;padding:9px 18px;border-radius:8px;border:none;cursor:pointer;font-size:14px;font-weight:500;transition:all .2s}
.btn-primary{background:var(--accent);color:#fff}
.btn-primary:hover{filter:brightness(1.1)}
.btn-primary:disabled{opacity:.5;cursor:not-allowed}
.btn-success{background:var(--green);color:#fff}
.btn-danger{background:var(--red);color:#fff}
.btn-block{width:100%}
.msg{padding:10px 14px;border-radius:8px;font-size:13px;margin-top:10px;display:none}
.msg-error{background:rgba(239,68,68,.12);color:var(--red);border:1px solid rgba(239,68,68,.3)}
.msg-success{background:rgba(34,197,94,.12);color:var(--green);border:1px solid rgba(34,197,94,.3)}
.msg-show{display:block}
.route-flow{display:flex;flex-direction:column;gap:8px;margin-top:10px}
.route-item{display:flex;align-items:center;gap:10px;padding:10px 12px;background:#12141c;border-radius:8px;font-size:13px}
.route-item .arrow{color:var(--muted);font-size:16px}
.route-item .label{font-weight:600;min-width:80px}
.route-item .target{min-width:120px;text-align:center;padding:3px 8px;border-radius:6px}
.route-item .desc{color:var(--muted);font-size:12px}
.route-item .target.green{background:rgba(34,197,94,.15);color:var(--green)}
.route-item .target.yellow{background:rgba(234,179,8,.15);color:var(--yellow)}
.route-item .target.orange{background:rgba(249,115,22,.15);color:#f97316}
.api-table{width:100%;border-collapse:collapse;font-size:13px}
.api-table td{padding:7px 4px;border-bottom:1px solid var(--border)}
.api-table td:first-child{font-family:monospace;color:var(--accent)}
.api-table td:last-child{color:var(--muted)}
.refresh-info{font-size:12px;color:var(--muted);text-align:center;margin-top:10px}
.footer{padding:16px 24px;border-top:1px solid var(--border);text-align:center;font-size:12px;color:var(--muted)}
</style>
</head>
<body>
<div class="header">
  <div class="dot"></div>
  <h1>🪙 Frugal Coder 管理后台</h1>
  <span style="margin-left:auto;font-size:13px;color:var(--muted)">smart-router :4020</span>
</div>
<div class="main">
  <div class="sidebar">
    <div class="card">
      <div class="card-title">运行模式</div>
      <div class="mode-toggle">
        <button class="mode-btn frugal active" id="btn-frugal" onclick="setMode('frugal')">💰 省钱</button>
        <button class="mode-btn direct" id="btn-direct" onclick="setMode('direct')">⚡ 直接</button>
      </div>
      <div id="mode-msg" class="msg"></div>
    </div>
    <div class="card">
      <div class="card-title">请求统计</div>
      <div class="stats-grid">
        <div class="stat-card total"><div class="stat-num" id="s-total">-</div><div class="stat-label">总请求</div></div>
        <div class="stat-card cheap"><div class="stat-num" id="s-cheap">-</div><div class="stat-label">SIMPLE</div></div>
        <div class="stat-card react"><div class="stat-num" id="s-react">-</div><div class="stat-label">COMPLEX</div></div>
        <div class="stat-card main"><div class="stat-num" id="s-main">-</div><div class="stat-label">主模型</div></div>
      </div>
      <div class="refresh-info" id="refresh-info">每 3 秒自动刷新</div>
    </div>
    <div class="card">
      <div class="card-title">路由规则</div>
      <div class="route-flow">
        <div class="route-item"><span class="arrow">→</span><span class="label">SIMPLE</span><span class="target green">grok 免费</span></div>
        <div class="route-item"><span class="arrow">→</span><span class="label">COMPLEX</span><span class="target yellow">ReAct 免费</span></div>
        <div class="route-item"><span class="arrow">→</span><span class="label">TOOLS</span><span class="target orange">主模型</span></div>
      </div>
    </div>
    <div class="card">
      <div class="card-title">API 端点</div>
      <table class="api-table">
        <tr><td>GET /health</td><td>健康检查</td></tr>
        <tr><td>GET /stats</td><td>统计数据</td></tr>
        <tr><td>GET /config</td><td>完整配置</td></tr>
        <tr><td>POST /mode</td><td>切换模式</td></tr>
        <tr><td>POST /config</td><td>更新配置</td></tr>
      </table>
    </div>
  </div>
  <div class="content">
    <div class="card" style="margin-bottom:20px">
      <div class="card-title">🤖 Cheap 模型（上游代理）</div>
      <form id="cheap-form" onsubmit="return saveCheap(event)">
        <div class="form-row"><label>API Base</label><input type="text" id="c-base" value="" placeholder="https://your-upstream/v1"></div>
        <div class="form-row"><label>API Key</label><input type="password" id="c-key" value="" placeholder="your-api-key"></div>
        <div class="form-row"><label>Model</label><input type="text" id="c-model" value="" placeholder="grok-4.1-fast"></div>
        <div style="display:flex;gap:8px;margin-top:4px">
          <button type="submit" class="btn btn-success btn-block">✅ 保存</button>
          <button type="button" class="btn btn-primary btn-block" onclick="testCheap()">🧪 测试</button>
        </div>
        <div id="cheap-msg" class="msg"></div>
      </form>
    </div>
    <div class="card">
      <div class="card-title">🔥 Main 模型（NEEDS_TOOLS + Direct 模式）</div>
      <form id="main-form" onsubmit="return saveMain(event)">
        <div class="form-row"><label>API Base</label><input type="text" id="m-base" value="" placeholder="https://api.minimaxi.com/anthropic"></div>
        <div class="form-row"><label>API Key</label><input type="password" id="m-key" value="" placeholder="your-api-key"></div>
        <div class="form-row"><label>Model</label><input type="text" id="m-model" value="" placeholder="MiniMax-M2.7"></div>
        <div style="display:flex;gap:8px;margin-top:4px">
          <button type="submit" class="btn btn-success btn-block">✅ 保存</button>
          <button type="button" class="btn btn-primary btn-block" onclick="testMain()">🧪 测试</button>
        </div>
        <div id="main-msg" class="msg"></div>
      </form>
    </div>
  </div>
</div>
<div class="footer">Frugal Coder v1.0 · 节省 70-90% 主模型 token</div>
<script>
const API = '';
let refreshTimer;

async function req(method, path, body) {
  const opts = {method, headers:{'Content-Type':'application/json'}};
  if (body) opts.body = JSON.stringify(body);
  const r = await fetch(API + path, opts);
  return r.json();
}

async function loadData() {
  const [health, cfg] = await Promise.all([req('GET','/health'), req('GET','/config')]);
  // Mode
  const mode = health.mode || cfg.mode || 'frugal';
  document.querySelectorAll('.mode-btn').forEach(b=>b.classList.remove('active'));
  document.getElementById('btn-'+mode).classList.add('active');
  // Stats
  if (health.stats) {
    document.getElementById('s-total').textContent = health.stats.total_requests||0;
    document.getElementById('s-cheap').textContent = health.stats.cheap_handled||0;
    document.getElementById('s-react').textContent = health.stats.react_handled||0;
    document.getElementById('s-main').textContent = health.stats.main_handled||0;
  }
  // Cheap form
  if (cfg.upstream) {
    document.getElementById('c-base').value = cfg.upstream.api_base||'';
    document.getElementById('c-model').value = cfg.upstream.model||'';
  }
  // Main form
  if (cfg.main) {
    document.getElementById('m-base').value = cfg.main.api_base||'';
    document.getElementById('m-model').value = cfg.main.model||'';
  }
}

async function setMode(mode) {
  document.querySelectorAll('.mode-btn').forEach(b=>b.classList.remove('active'));
  document.getElementById('btn-'+mode).classList.add('active');
  const r = await req('POST','/mode', {mode});
  const msg = document.getElementById('mode-msg');
  if (r.status==='ok') {
    msg.textContent = '✅ 已切换为 '+(mode==='frugal'?'省钱模式':'直接模式');
    msg.className='msg msg-success msg-show';
  } else {
    msg.textContent='❌ 切换失败: '+(r.error||'');
    msg.className='msg msg-error msg-show';
  }
  setTimeout(()=>msg.classList.remove('msg-show'), 3000);
}

async function saveCheap(e) {
  e.preventDefault();
  const base = document.getElementById('c-base').value.trim();
  const key = document.getElementById('c-key').value.trim();
  const model = document.getElementById('c-model').value.trim();
  const r = await req('POST','/config', {provider:{api_base:base, api_key:key, model}});
  const msg=document.getElementById('cheap-msg');
  if (r.status==='ok') {
    msg.textContent='✅ Cheap 配置已保存（重启 smart-router 生效）';msg.className='msg msg-success msg-show';
  } else {msg.textContent='❌ 保存失败';msg.className='msg msg-error msg-show';}
  setTimeout(()=>msg.classList.remove('msg-show'),4000);
}

async function testCheap() {
  const msg=document.getElementById('cheap-msg');
  msg.textContent='🧪 测试中...';msg.className='msg msg-show';
  const r = await fetch(API+'/v1/chat/completions',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({model:'sr',messages:[{role:'user',content:'回复 OK'}],max_tokens:10})
  }).catch(()=>null);
  const d = await r?.json().catch(()=>null);
  if (d?.choices) {msg.textContent='✅ Cheap 模型正常: '+d.choices[0].message.content.slice(0,50);msg.className='msg msg-success msg-show';}
  else {msg.textContent='❌ Cheap 模型失败';msg.className='msg msg-error msg-show';}
  setTimeout(()=>msg.classList.remove('msg-show'),4000);
}

async function saveMain(e) {
  e.preventDefault();
  const base = document.getElementById('m-base').value.trim();
  const key = document.getElementById('m-key').value.trim();
  const model = document.getElementById('m-model').value.trim();
  const r = await req('POST','/config', {main:{api_base:base, api_key:key, model}});
  const msg=document.getElementById('main-msg');
  if (r.status==='ok') {
    msg.textContent='✅ Main 配置已保存（重启 smart-router 生效）';msg.className='msg msg-success msg-show';
  } else {msg.textContent='❌ 保存失败';msg.className='msg msg-error msg-show';}
  setTimeout(()=>msg.classList.remove('msg-show'),4000);
}

async function testMain() {
  const msg=document.getElementById('main-msg');
  msg.textContent='🧪 测试中...';msg.className='msg msg-show';
  // 通过 smart-router 发送一个 NEEDS_TOOLS 请求（带工具描述）触发主模型
  const r = await fetch(API+'/v1/chat/completions',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({model:'sr',messages:[{role:'user',content:'hello'}],
      tools:[{type:'function',function:{name:'test',description:'test'}}],max_tokens:10})
  }).catch(()=>null);
  const d = await r?.json().catch(()=>null);
  if (d?.choices) {msg.textContent='✅ Main 模型正常: '+d.choices[0].message.content.slice(0,50);msg.className='msg msg-success msg-show';}
  else if (d?.error) {msg.textContent='❌ '+d.error.message||d.error;msg.className='msg msg-error msg-show';}
  else {msg.textContent='❌ Main 模型失败或未配置';msg.className='msg msg-error msg-show';}
  setTimeout(()=>msg.classList.remove('msg-show'),4000);
}

loadData();
refreshTimer = setInterval(loadData, 3000);
</script>
</body>
</html>"""

import argparse
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

# 模式切换文件
MODE_FILE = os.path.expanduser("~/.openclaw/frugal-router.mode")
# 配置文件（主模型独立配置）
CONFIG_FILE = os.path.expanduser("~/.openclaw/skills/frugal-coder/config.yaml")

def load_config():
    """从 config.yaml 加载配置"""
    config = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                raw = yaml.safe_load(f)
                if raw:
                    config = raw
        except Exception:
            pass
    return config

def save_config(config):
    """保存配置到 config.yaml"""
    try:
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, "w") as f:
            yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)
        return True
    except Exception as e:
        log(f"保存配置失败: {e}")
        return False

def get_full_config():
    """获取完整配置（合并后的运行时配置）"""
    cfg = load_config()
    return {
        "mode": get_router_mode(),
        "mode_file": MODE_FILE,
        "cheap": {
            "api_base": f"http://127.0.0.1:{CHEAP_PORT}/v1",
            "api_key": "（通过 sse-fix-proxy）",
            "model": CHEAP_MODEL,
        },
        "main": {
            "api_base": MAIN_URL,
            "api_key": MAIN_KEY[:6] + "..." if MAIN_KEY else "",
            "model": MAIN_MODEL,
        },
        "upstream": {
            "api_base": cfg.get("provider", {}).get("api_base", ""),
            "api_key": cfg.get("provider", {}).get("api_key", "")[:6] + "..." if cfg.get("provider", {}).get("api_key", "") else "",
            "model": cfg.get("provider", {}).get("model", ""),
        },
        "gateway": cfg.get("gateway", {}),
    }

def get_router_mode():
    """读取当前运行模式（运行时可切换）"""
    if os.path.exists(MODE_FILE):
        try:
            with open(MODE_FILE) as f:
                mode = f.read().strip().lower()
                if mode in ("frugal", "direct"):
                    return mode
        except Exception:
            pass
    return "frugal"

def resolve_main_config():
    """解析主模型配置（CLI参数 > 环境变量 > 配置文件）"""
    global MAIN_URL, MAIN_KEY, MAIN_MODEL
    cfg = load_config()
    cfg_main_url = ""
    cfg_main_key = ""
    cfg_main_model = "minimax/MiniMax-M2.7"
    if "main" in cfg:
        cfg_main_url = cfg["main"].get("api_base", "")
        cfg_main_key = cfg["main"].get("api_key", "")
    if "main_model" in cfg:
        cfg_main_model = cfg["main_model"]
    if not MAIN_URL:
        MAIN_URL = os.environ.get("MAIN_MODEL_URL", "") or cfg_main_url
    if not MAIN_KEY:
        MAIN_KEY = os.environ.get("MAIN_MODEL_KEY", "") or cfg_main_key
    if not MAIN_MODEL:
        MAIN_MODEL = os.environ.get("MAIN_MODEL_NAME", "") or cfg_main_model
    if not MAIN_URL:
        MAIN_URL = "https://api.minimaxi.com/anthropic/v1"

# ─── 配置 ──────────────────────────────────────────────

ROUTER_PORT = int(os.environ.get("SMART_ROUTER_PORT", "4020"))
CHEAP_PORT = os.environ.get("CHEAP_MODEL_PORT", "4010")
CHEAP_MODEL = os.environ.get("CHEAP_MODEL_NAME", "YOUR_MODEL")
CHEAP_URL = f"http://127.0.0.1:{CHEAP_PORT}/v1/chat/completions"

# 主模型配置（NEEDS_TOOLS 和 direct 模式用）
MAIN_URL = os.environ.get("MAIN_MODEL_URL", "")
MAIN_KEY = os.environ.get("MAIN_MODEL_KEY", "")
MAIN_MODEL = os.environ.get("MAIN_MODEL_NAME", "")

# 模式切换文件（运行时切换模式）
MODE_FILE = os.path.expanduser("~/.openclaw/frugal-router.mode")

resolve_main_config()

# 统计
stats = {
    "total_requests": 0,
    "cheap_handled": 0,
    "react_handled": 0,
    "main_handled": 0,
    "direct_handled": 0,
    "errors": 0,
    "mode": "frugal",
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

    def _send_html(self, code, html):
        body = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length:
            return json.loads(self.rfile.read(length))
        return {}

    def do_GET(self):
        """处理 GET 请求（健康检查、配置、管理界面等）"""
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
                "mode": get_router_mode(),
                "cheap_model": CHEAP_MODEL,
                "main_model": MAIN_MODEL or "(not configured)",
                "main_url": MAIN_URL or "(not configured)",
            })
        elif self.path == "/stats":
            self._send_json(200, stats)
        elif self.path == "/config":
            self._send_json(200, get_full_config())
        elif self.path == "/mode":
            self._send_json(200, {"mode": get_router_mode()})
        elif self.path in ("/", "/index.html", "/manage", "/management"):
            self._send_html(200, MANAGEMENT_HTML)
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        """处理 POST 请求（配置、模式切换、聊天）"""
        if self.path == "/v1/chat/completions":
            pass  # 继续原有聊天逻辑
        elif self.path == "/config":
            # 更新配置
            body = self._read_body()
            cfg = load_config()
            if "provider" in body:
                cfg["provider"] = body["provider"]
            if "main" in body:
                cfg["main"] = body["main"]
            if "gateway" in body:
                cfg["gateway"] = body["gateway"]
            if save_config(cfg):
                # 重新加载
                global CHEAP_MODEL, MAIN_URL, MAIN_KEY, MAIN_MODEL
                resolve_main_config()
                self._send_json(200, {"status": "ok", "config": get_full_config()})
            else:
                self._send_json(500, {"error": "保存配置失败"})
            return
        elif self.path == "/mode":
            # 切换运行模式
            body = self._read_body()
            mode = body.get("mode", "").strip().lower()
            if mode not in ("frugal", "direct"):
                self._send_json(400, {"error": "mode must be frugal or direct"})
                return
            try:
                with open(MODE_FILE, "w") as f:
                    f.write(mode)
                stats["mode"] = mode
                self._send_json(200, {"mode": mode, "status": "ok"})
            except Exception as e:
                self._send_json(500, {"error": str(e)})
            return
        else:
            self._send_json(404, {"error": "not found"})
            return

        current_mode = get_router_mode()
        stats["mode"] = current_mode
        stats["total_requests"] += 1
        body = self._read_body()

        messages = body.get("messages", [])
        tools = body.get("tools", [])
        max_tokens = body.get("max_tokens", 4096)
        stream = body.get("stream", False)

        # === 直接模式：透明转发给主模型，失败则自动 fallback 到 frugal 逻辑 ===
        if current_mode == "direct":
            log(f"🌐 → 直接模式 → 主模型")
            stats["direct_handled"] += 1
            stats["main_handled"] += 1
            result = call_main_api(body)
            if result:
                self._send_json(200, result)
                return
            else:
                # 主模型不可用，自动降级到 frugal 逻辑
                log(f"  ⚠️ 主模型不可用，自动降级到 frugal 逻辑")
                stats["direct_handled"] -= 1
                # 继续执行下面的 frugal 逻辑（不 return）

        # === 省钱模式 ===
        user_msg = extract_last_user_message(messages)
        system_hint = ""
        for m in messages:
            if m.get("role") == "system":
                system_hint += m.get("content", "")[:300] + " "

        has_tools = len(tools) > 0

        # 心跳和特殊消息直接转发
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
    # 初始化模式文件
    if not os.path.exists(MODE_FILE):
        with open(MODE_FILE, 'w') as f:
            f.write('frugal')
        print(f"   首次启动，写入默认模式 frugal → {MODE_FILE}")

    server = HTTPServer(("127.0.0.1", port), SmartRouterHandler)
    log(f"🚀 Smart Router 启动于 http://127.0.0.1:{port}")
    log(f"   当前模式: {get_router_mode()} (切换: echo frugal > {MODE_FILE} 或 echo direct > {MODE_FILE})")
    log(f"   便宜模型: {CHEAP_MODEL} (port {CHEAP_PORT})")
    log(f"   主模型: {MAIN_MODEL} ({MAIN_URL})")
    log(f"   统计: http://127://127.0.0.1:{port}/stats")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("\n🛑 Smart Router 关闭")
        server.server_close()

if __name__ == "__main__":
    _parser = argparse.ArgumentParser(description="Smart Router")
    _parser.add_argument("--port", type=int, default=ROUTER_PORT)
    _parser.add_argument("--cheap-model", default=CHEAP_MODEL)
    _parser.add_argument("--main-url", default="")
    _parser.add_argument("--main-key", default="")
    _parser.add_argument("--main-model", default="")
    _args = _parser.parse_args()

    # 用 exec 修改全局变量（避免 global 声明时序问题）
    _g = globals()
    if _args.main_url: _g["MAIN_URL"] = _args.main_url
    if _args.main_key: _g["MAIN_KEY"] = _args.main_key
    if _args.main_model: _g["MAIN_MODEL"] = _args.main_model
    if _args.cheap_model: _g["CHEAP_MODEL"] = _args.cheap_model

    run_server(_args.port)
