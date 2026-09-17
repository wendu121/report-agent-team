# tools/data_proc.py · 数据处理 / 计算（M4，Analyst 工具）
#
# 安全原则：**绝不 eval 用户字符串**。采用 AST 白名单求值，
# 仅允许数值常量 / 四则运算 / 幂 / 取模 / 括号 / 白名单函数。
# 禁止名称访问、属性访问、下标、lambda、推导式、任意函数调用。

from __future__ import annotations

import ast
import operator
import statistics
from typing import Optional

from . import ToolError

_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
}
_UNARYOPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _agg(fn):
    """让 min/max/sum 同时支持 f([...]) 与 f(a, b, ...)。"""

    def _call(*args):
        if len(args) == 1 and isinstance(args[0], (list, tuple)):
            return fn(list(args[0]))
        return fn(list(args))
    return _call


_FUNCS = {
    "sum": _agg(lambda xs: sum(xs)),
    "mean": _agg(lambda xs: statistics.mean(xs)),
    "median": _agg(lambda xs: statistics.median(xs)),
    "min": _agg(lambda xs: min(xs)),
    "max": _agg(lambda xs: max(xs)),
    "len": _agg(lambda xs: len(xs)),
    "abs": lambda x: abs(x),
    "round": lambda x, n=2: round(x, n),
}


def safe_eval(expr: str):
    """在 AST 白名单内求值算术表达式；任何越界语法直接 ToolError。"""
    if not isinstance(expr, str) or not expr.strip():
        raise ToolError("表达式为空")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ToolError(f"表达式语法错误: {e}")

    def _eval(node):
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise ToolError("只允许数值常量")
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
            return _BINOPS[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARYOPS:
            return _UNARYOPS[type(node.op)](_eval(node.operand))
        if isinstance(node, (ast.List, ast.Tuple)):
            return [_eval(e) for e in node.elts]
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
                raise ToolError("仅允许白名单函数: " + ", ".join(sorted(_FUNCS)))
            return _FUNCS[node.func.id](*[_eval(a) for a in node.args])
        raise ToolError(f"表达式含不允许的语法: {type(node).__name__}")

    return _eval(tree.body)


class DataProcTool:
    """Analyst 的计算工具。执行 LLM 在 tool_requests 中声明的表达式。"""

    def __init__(self, enabled: bool = True, max_requests: int = 20):
        self.enabled = enabled
        self.max_requests = max_requests

    def run_requests(self, requests: list, agent: str = "Analyst"):
        """执行 [{"expr": "..."} ...]，返回 (results, tool_status_entries)。

        单条失败不影响其余（失败隔离）；结果回注给 LLM 做二次生成。
        """
        results: list[dict] = []
        status: list[dict] = []

        if not self.enabled:
            return results, [{"agent": agent, "tool": "data_proc", "ok": False,
                              "error": "data_proc 已禁用"}]
        if not requests:
            return results, status
        if len(requests) > self.max_requests:
            requests = requests[:self.max_requests]
            status.append({"agent": agent, "tool": "data_proc", "ok": False,
                           "error": f"请求数超出上限 {self.max_requests}，已截断"})

        for req in requests:
            expr = req.get("expr") if isinstance(req, dict) else req
            try:
                value = safe_eval(str(expr))
                results.append({"expr": expr, "ok": True, "value": value})
                status.append({"agent": agent, "tool": "data_proc", "ok": True})
            except ToolError as e:
                results.append({"expr": expr, "ok": False, "error": str(e)})
                status.append({"agent": agent, "tool": "data_proc", "ok": False,
                               "error": f"{expr} :: {e}"})
            except ZeroDivisionError:
                results.append({"expr": expr, "ok": False, "error": "除零"})
                status.append({"agent": agent, "tool": "data_proc", "ok": False,
                               "error": f"{expr} :: 除零"})
        return results, status
