#!/usr/bin/env python3
"""
react-orchestrate.py — 通用 ReAct 循环编排器（模型无关）

Think → Decide → Execute → Observe → Loop
通过 frugal-gateway 调用任意便宜/免费模型，
自主决策下一步调用什么技能，直到任务完成。

用法:
  # CLI 模式（使用内置通用技能库）
  python3 react-orchestrate.py "比较 Python 和 Go 的 Web 性能差异"

  # 指定最大循环次数
  python3 react-orchestrate.py --max-iter 6 --verbose "分析某技术方案"

  # JSON 输出
  python3 react-orchestrate.py --json "复杂任务"

环境变量:
  FRUGAL_PORT    - 网关端口（默认 4010）
  FRUGAL_MODEL   - 模型 ID（通过环境变量或参数指定）
  REACT_MAX_ITER - 最大循环次数（默认 8）
"""

import json
import os
import subprocess
import sys
import urllib.request
import urllib.error
import argparse
import textwrap
from dataclasses import dataclass, field
from typing import Callable, Optional
from datetime import datetime

# ─── 配置 ──────────────────────────────────────────────

PORT = os.environ.get("FRUGAL_PORT", "4010")
MODEL = os.environ.get("FRUGAL_MODEL", "YOUR_MODEL")
GATEWAY = f"http://127.0.0.1:{PORT}/v1/chat/completions"
MAX_ITER = int(os.environ.get("REACT_MAX_ITER", "8"))

# ─── 数据类 ──────────────────────────────────────────────


@dataclass
class SkillResult:
    """技能执行结果"""
    success: bool
    data: str
    skill_name: str = ""
    error: str = ""
    preview: str = ""

    def __post_init__(self):
        if not self.preview:
            self.preview = self.data[:300] if self.success else f"[ERROR] {self.error}"


@dataclass
class Decision:
    """LLM 决策"""
    next_action: str          # 技能名称 或 "FINAL_ANSWER"
    reason: str = ""
    skill_params: dict = field(default_factory=dict)
    confidence: float = 0.0
    thought: str = ""         # LLM 的思考过程


@dataclass
class HistoryEntry:
    """历史步骤记录"""
    step: int
    decision: Decision
    result: Optional[SkillResult] = None
    observation: str = ""


@dataclass
class OrchestratorResult:
    """编排最终结果"""
    task: str
    final_answer: str
    history: list = field(default_factory=list)
    iterations: int = 0
    success: bool = True
    summary: str = ""
    model: str = ""

# ─── Skill 抽象 ─────────────────────────────────────────


class Skill:
    """可执行的最小技能单元"""

    def __init__(
        self,
        name: str,
        description: str,
        execute: Callable[[dict, dict], SkillResult],
        params_schema: dict = None,
        priority: int = 5,
        cost: float = 0.0,
        reliability: float = 0.9,
    ):
        self.name = name
        self.description = description
        self.execute_fn = execute
        self.params_schema = params_schema or {}
        self.priority = priority
        self.cost = cost
        self.reliability = reliability

    def execute(self, params: dict, context: dict) -> SkillResult:
        try:
            result = self.execute_fn(params, context)
            if isinstance(result, str):
                return SkillResult(success=True, data=result, skill_name=self.name)
            return result
        except Exception as e:
            return SkillResult(
                success=False, data="", skill_name=self.name, error=str(e)
            )

    def to_description(self) -> str:
        params_hint = ""
        if self.params_schema:
            params_hint = f"，参数: {json.dumps(self.params_schema, ensure_ascii=False)}"
        return (f"- {self.name}（优先级{self.priority}，可靠度{self.reliability}）: "
                f"{self.description}{params_hint}")


class SkillRegistry:
    """技能注册表"""

    def __init__(self, name: str = "default"):
        self.name = name
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill):
        self._skills[skill.name] = skill

    def get(self, name: str) -> Optional[Skill]:
        return self._skills.get(name)

    def all_skills(self) -> list[Skill]:
        return sorted(self._skills.values(), key=lambda s: s.priority)

    def skill_list_text(self) -> str:
        return "\n".join(s.to_description() for s in self.all_skills())

    def skill_names(self) -> list[str]:
        return list(self._skills.keys())

# ─── LLM 调用（模型无关） ──────────────────────────────


def call_llm(messages: list, max_tokens: int = 4096) -> str:
    """通过 frugal-gateway 调用配置的便宜/免费模型"""
    payload = json.dumps({
        "model": MODEL,
        "messages": messages,
        "stream": False,
        "max_tokens": max_tokens,
    }).encode("utf-8")

    req = urllib.request.Request(
        GATEWAY,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if "choices" in data and data["choices"]:
                return data["choices"][0]["message"]["content"]
            return f"[ERROR] Unexpected response: {json.dumps(data)[:200]}"
    except urllib.error.HTTPError as e:
        return f"[ERROR] HTTP {e.code}: {e.read().decode()[:300]}"
    except Exception as e:
        return f"[ERROR] {e}"


def exec_cmd(cmd: str, timeout: int = 60) -> str:
    """执行 shell 命令"""
    try:
        r = subprocess.run(
            ["bash", "-c", cmd],
            capture_output=True, text=True, timeout=timeout
        )
        if r.returncode == 0:
            return r.stdout or "(no output)"
        return f"[EXIT {r.returncode}] {r.stderr[:500]}"
    except subprocess.TimeoutExpired:
        return "[TIMEOUT]"
    except Exception as e:
        return f"[ERROR] {e}"

# ─── 内置技能工厂 ──────────────────────────────────────


def make_web_search_skill() -> Skill:
    """Web 搜索技能（通过 LLM 知识 + 外部搜索）"""
    def execute(params: dict, ctx: dict) -> SkillResult:
        query = params.get("query", "")
        limit = params.get("limit", 5)
        result = call_llm([
            {"role": "system",
             "content": "你是一个研究助手。基于你的知识，尽可能准确地回答搜索式问题。如果不确定请说明。"},
            {"role": "user",
             "content": f"搜索主题: {query}\n请列出最相关的 {limit} 条信息，每条包含标题、来源和摘要。"}
        ], max_tokens=2048)
        return SkillResult(success=True, data=result, skill_name="web_search")

    return Skill(
        name="web_search",
        description="搜索最新信息、新闻、公告（通用搜索）",
        params_schema={"query": "搜索关键词", "limit": "结果数量（默认5）"},
        priority=1, cost=0, reliability=0.85,
        execute=execute,
    )


def make_code_execute_skill() -> Skill:
    """代码执行技能"""
    def execute(params: dict, ctx: dict) -> SkillResult:
        code = params.get("code", "")
        lang = params.get("language", "python")
        timeout = params.get("timeout", 30)

        if lang == "python":
            cmd = f'python3 -c {json.dumps(code)}'
        else:
            cmd = code

        result = exec_cmd(cmd, timeout=timeout)
        success = not result.startswith(("[ERROR]", "[TIMEOUT]", "[EXIT"))
        return SkillResult(success=success, data=result, skill_name="code_execute")

    return Skill(
        name="code_execute",
        description="执行代码（Python 或 shell 命令），获取运行结果",
        params_schema={
            "code": "要执行的代码", "language": "python/bash（默认python）",
            "timeout": "超时秒数（默认30）"
        },
        priority=2, cost=0, reliability=0.95,
        execute=execute,
    )


def make_llm_analyze_skill() -> Skill:
    """LLM 深度分析技能"""
    def execute(params: dict, ctx: dict) -> SkillResult:
        prompt = params.get("prompt", "")
        context_data = params.get("context", "")
        system = params.get("system", "你是一个专业的分析师。请基于提供的数据进行深入分析。")

        messages = [{"role": "system", "content": system}]
        if context_data:
            messages.append({
                "role": "user",
                "content": f"背景数据:\n{context_data}\n\n问题: {prompt}"
            })
        else:
            messages.append({"role": "user", "content": prompt})

        result = call_llm(messages, max_tokens=4096)
        return SkillResult(success=True, data=result, skill_name="llm_analyze")

    return Skill(
        name="llm_analyze",
        description="调用便宜模型进行深度分析、推理、总结（最灵活的认知技能）",
        params_schema={
            "prompt": "分析问题", "context": "相关数据（可选）",
            "system": "系统提示（可选）"
        },
        priority=3, cost=0, reliability=0.9,
        execute=execute,
    )


def make_file_read_skill() -> Skill:
    """文件读取技能"""
    def execute(params: dict, ctx: dict) -> SkillResult:
        path = params.get("path", "")
        lines = params.get("lines", 100)
        result = exec_cmd(f'head -{lines} "{path}"', timeout=10)
        success = not result.startswith("[ERROR]")
        return SkillResult(success=success, data=result, skill_name="file_read")

    return Skill(
        name="file_read",
        description="读取文件内容（代码、配置、日志等）",
        params_schema={"path": "文件路径", "lines": "读取行数（默认100）"},
        priority=2, cost=0, reliability=0.99,
        execute=execute,
    )


def make_aider_skill() -> Skill:
    """Aider 代码编辑技能"""
    def execute(params: dict, ctx: dict) -> SkillResult:
        message = params.get("message", "")
        files = params.get("files", [])
        cwd = params.get("cwd", os.getcwd())

        files_arg = " ".join(files) if files else ""
        cmd = (
            f'cd {cwd} && '
            f'OPENAI_API_BASE="http://127.0.0.1:{PORT}/v1" '
            f'OPENAI_API_KEY="any" '
            f'aider --model openai/{MODEL} --no-stream '
            f'--no-show-model-warnings --no-auto-commits '
            f'--message {json.dumps(message)} {files_arg}'
        )

        result = exec_cmd(cmd, timeout=120)
        success = not result.startswith(("[ERROR]", "[TIMEOUT]"))
        return SkillResult(success=success, data=result, skill_name="aider_code")

    return Skill(
        name="aider_code",
        description="使用 Aider 编辑/创建代码文件（需要 git 仓库）",
        params_schema={"message": "编辑指令", "files": "文件列表", "cwd": "工作目录"},
        priority=1, cost=0, reliability=0.85,
        execute=execute,
    )


def make_http_request_skill() -> Skill:
    """HTTP 请求技能"""
    def execute(params: dict, ctx: dict) -> SkillResult:
        url = params.get("url", "")
        method = params.get("method", "GET").upper()
        headers = params.get("headers", {})
        body = params.get("body", "")

        header_args = " ".join(f'-H "{k}: {v}"' for k, v in headers.items())
        body_arg = f"-d '{body}'" if body else ""

        cmd = f'curl -s -X {method} {header_args} {body_arg} "{url}"'
        result = exec_cmd(cmd, timeout=30)
        success = not result.startswith("[ERROR]")
        return SkillResult(success=success, data=result, skill_name="http_request")

    return Skill(
        name="http_request",
        description="发送 HTTP 请求（GET/POST），获取 API 数据",
        params_schema={
            "url": "请求 URL", "method": "GET/POST（默认GET）",
            "headers": "请求头", "body": "请求体"
        },
        priority=2, cost=0, reliability=0.9,
        execute=execute,
    )

# ─── 内置技能库 ─────────────────────────────────────────


def default_registry() -> SkillRegistry:
    """创建默认通用技能库"""
    registry = SkillRegistry("general")
    registry.register(make_web_search_skill())
    registry.register(make_code_execute_skill())
    registry.register(make_llm_analyze_skill())
    registry.register(make_file_read_skill())
    registry.register(make_aider_skill())
    registry.register(make_http_request_skill())
    return registry

# ─── ReAct 编排器 ──────────────────────────────────────


class ReActOrchestrator:
    """通用 ReAct 循环编排器（模型无关）"""

    def __init__(
        self,
        registry: SkillRegistry,
        model: str = MODEL,
        max_iterations: int = MAX_ITER,
        verbose: bool = False,
    ):
        self.registry = registry
        self.model = model
        self.max_iterations = max_iterations
        self.verbose = verbose
        self.history: list[HistoryEntry] = []

    def _build_think_prompt(self, task: str, failed_skills: list[str]) -> str:
        """构建思考+决策上下文"""
        lines = [f"【任务】{task}\n"]

        if self.history:
            lines.append(f"【已完成步骤】{len(self.history)} 个:")
            for h in self.history[-6:]:
                status = "✅" if (h.result and h.result.success) else "❌"
                lines.append(
                    f"  {h.step}. {status} [{h.decision.next_action}] "
                    f"{h.decision.reason[:80]}"
                )
                if h.result:
                    preview = h.result.preview[:150].replace("\n", " ")
                    lines.append(f"     结果: {preview}")
        else:
            lines.append("【已完成步骤】0 个（刚开始）")

        if failed_skills:
            lines.append(f"\n【失败过的技能】（不要重试同样的参数）:")
            for s in failed_skills[-3:]:
                lines.append(f"  - {s}")

        lines.append(f"\n【可用技能】:")
        lines.append(self.registry.skill_list_text())

        return "\n".join(lines)

    def _parse_decision(self, response: str) -> Decision:
        """解析 LLM 返回的决策 JSON"""
        response = response.strip()

        # 去掉 markdown 代码块
        if response.startswith("```"):
            lines = response.split("\n")
            response = "\n".join(lines[1:])
            if response.endswith("```"):
                response = response[:-3]

        # 尝试找到 JSON 块
        json_start = response.find("{")
        json_end = response.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            json_str = response[json_start:json_end]
            try:
                d = json.loads(json_str)
                return Decision(
                    next_action=d.get("next_action", "FINAL_ANSWER"),
                    reason=d.get("reason", ""),
                    skill_params=d.get("skill_params", {}),
                    confidence=d.get("confidence", 0.5),
                    thought=d.get("thought", ""),
                )
            except json.JSONDecodeError:
                pass

        # 解析失败，当作 FINAL_ANSWER
        return Decision(
            next_action="FINAL_ANSWER",
            reason="无法解析决策 JSON，直接返回模型回复",
            confidence=0.5,
            thought=response[:500],
        )

    def _think_and_decide(self, task: str, failed_skills: list[str]) -> Decision:
        """LLM 思考并决定下一步"""
        context = self._build_think_prompt(task, failed_skills)

        prompt = f"""{context}

请思考：
1. 当前已经掌握了哪些信息？
2. 还需要什么数据？
3. 下一步应该调用哪个技能？还是已经有足够信息给出最终答案？

请只以 JSON 格式回答（不要其他文字）：
{{"thought": "你的思考过程", "next_action": "技能名称 或 FINAL_ANSWER", "reason": "为什么选这个", "skill_params": {{"参数": "值"}}, "confidence": 0.9}}"""

        response = call_llm([
            {"role": "system",
             "content": "你是一个 ReAct 循环的决策引擎。你的工作是分析当前状态，决定下一步行动。每次只做一个决策。"},
            {"role": "user", "content": prompt},
        ], max_tokens=2048)

        return self._parse_decision(response)

    def _synthesize(self, task: str) -> str:
        """LLM 综合所有步骤，生成最终报告"""
        step_summaries = []
        for h in self.history:
            status = "✅" if (h.result and h.result.success) else "❌"
            step_summaries.append(
                f"步骤{h.step}. {status} [{h.decision.next_action}]\n"
                f"  原因: {h.decision.reason}\n"
                f"  结果: {h.result.preview[:300] if h.result else '无结果'}"
            )

        prompt = f"""【任务】{task}

【分析过程（{len(self.history)} 步）】:
{chr(10).join(step_summaries)}

请基于以上所有步骤收集到的数据，输出完整的最终报告。
要求：
- 数据具体，结论明确
- 如果某些数据缺失，明确指出
- 客观中立，基于事实"""

        return call_llm([
            {"role": "system",
             "content": "你是一个专业的综合分析师。请基于所有步骤的结果，生成结构清晰的最终报告。"},
            {"role": "user", "content": prompt},
        ], max_tokens=4096)

    def run(self, task: str, context: dict = None) -> OrchestratorResult:
        """运行 ReAct 循环：Think → Decide → Execute → Observe → Loop"""
        self.history = []
        failed_skills = []
        context = context or {}

        if self.verbose:
            print(f"\n🎯 任务: {task}")
            print(f"🧠 模型: {self.model}")
            print(f"📋 可用技能: {', '.join(self.registry.skill_names())}")
            print(f"🔄 最大循环: {self.max_iterations} 次\n")

        for iteration in range(self.max_iterations):
            step = iteration + 1

            # === 1. THINK + DECIDE ===
            if self.verbose:
                print(f"─── 步骤 {step}: 思考中... ───")

            decision = self._think_and_decide(task, failed_skills)

            if self.verbose:
                print(f"  🧠 思考: {decision.thought[:100]}")
                print(f"  📌 决策: {decision.next_action}")
                print(f"  📝 原因: {decision.reason[:100]}")

            # === 2. 检查是否已完成 ===
            if decision.next_action == "FINAL_ANSWER":
                if self.verbose:
                    print(f"\n✅ 判断已有足够信息，生成最终报告...")
                final_answer = self._synthesize(task)
                return OrchestratorResult(
                    task=task,
                    final_answer=final_answer,
                    history=self.history,
                    iterations=step,
                    success=True,
                    summary=f"共 {step} 步完成",
                    model=self.model,
                )

            # === 3. EXECUTE ===
            skill = self.registry.get(decision.next_action)
            if not skill:
                if self.verbose:
                    print(f"  ❌ 技能不存在: {decision.next_action}")
                failed_skills.append(decision.next_action)
                self.history.append(HistoryEntry(
                    step=step,
                    decision=decision,
                    result=SkillResult(
                        success=False, data="",
                        skill_name=decision.next_action,
                        error=f"技能 '{decision.next_action}' 不存在"
                    ),
                ))
                continue

            if self.verbose:
                params_preview = json.dumps(
                    decision.skill_params, ensure_ascii=False)[:100]
                print(f"  ⚙️  执行: {decision.next_action}({params_preview})")

            result = skill.execute(decision.skill_params, context)

            if self.verbose:
                status = "✅" if result.success else "❌"
                print(f"  {status} 结果: {result.preview[:150]}")

            # === 4. OBSERVE ===
            self.history.append(HistoryEntry(
                step=step,
                decision=decision,
                result=result,
            ))

            if not result.success:
                failed_skills.append(decision.next_action)

        # === 循环结束，强制综合 ===
        if self.verbose:
            print(f"\n⚠️ 达到最大循环次数 ({self.max_iterations})，强制生成报告...")

        final_answer = self._synthesize(task)
        return OrchestratorResult(
            task=task,
            final_answer=final_answer,
            history=self.history,
            iterations=self.max_iterations,
            success=True,
            summary=f"达到最大循环 {self.max_iterations} 次后强制完成",
            model=self.model,
        )

# ─── CLI 入口 ──────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="ReAct 循环编排器 — 通用模型自主决策完成复杂任务",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
        示例:
          python3 react-orchestrate.py "比较 Python 和 Rust 的性能差异"
          python3 react-orchestrate.py --verbose --max-iter 6 "调查某个技术方案"
          python3 react-orchestrate.py --json "复杂的多步分析任务"

        环境变量:
          FRUGAL_MODEL=YOUR_MODEL       # 任意便宜模型
          FRUGAL_MODEL=deepseek-coder   # DeepSeek
          FRUGAL_MODEL=qwen2.5:7b       # Ollama 本地
        """),
    )
    parser.add_argument("task", help="要完成的任务描述")
    parser.add_argument("--model", default=MODEL,
                        help=f"模型 ID（默认 {MODEL}，通过 FRUGAL_MODEL 环境变量配置）")
    parser.add_argument("--max-iter", type=int, default=MAX_ITER,
                        help=f"最大循环次数（默认 {MAX_ITER}）")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="详细输出每个步骤")
    parser.add_argument("--json", action="store_true",
                        help="JSON 格式输出结果")

    args = parser.parse_args()

    # 选择技能库
    registry = default_registry()

    # 创建编排器
    orch = ReActOrchestrator(
        registry=registry,
        model=args.model,
        max_iterations=args.max_iter,
        verbose=args.verbose,
    )

    # 运行
    result = orch.run(args.task)

    # 输出
    if args.json:
        output = {
            "task": result.task,
            "model": result.model,
            "final_answer": result.final_answer,
            "iterations": result.iterations,
            "success": result.success,
            "steps": [
                {
                    "step": h.step,
                    "action": h.decision.next_action,
                    "reason": h.decision.reason,
                    "success": h.result.success if h.result else False,
                    "preview": h.result.preview[:200] if h.result else "",
                }
                for h in result.history
            ],
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(f"\n{'='*60}")
        print(f"🎯 任务: {result.task}")
        print(f"🧠 模型: {result.model}")
        print(f"📊 步骤: {result.iterations} | 状态: {'✅ 完成' if result.success else '❌ 失败'}")
        print(f"{'='*60}\n")
        print(result.final_answer)
        if result.history:
            print(f"\n{'─'*40}")
            print("📋 步骤详情:")
            for h in result.history:
                status = "✅" if (h.result and h.result.success) else "❌"
                print(f"  {h.step}. {status} [{h.decision.next_action}] "
                      f"{h.decision.reason[:60]}")


if __name__ == "__main__":
    main()
