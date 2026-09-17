"""聊天体受限沙箱（T44）：run_python / run_shell。

安全模型（诚实声明，不夸大）：
- 工作目录固定为项目根 `.chat_sandbox/`（与代码/配置/源码目录隔离），gitignore。
- run_python：在**独立子进程 + 隔离模式(-I)** 里以**受限内置 + 白名单 import** 执行，
  `os`/`subprocess`/`shutil`/`sys`/文件 builtins 均不可达 → 无法 `os.system`/`rm -rf`/
  读写沙箱外文件。子进程 15s 超时即终止（防死循环挂住 API 进程）。
- run_shell：固定 cwd + 15s 超时 + 输出截断 + **危险命令拦截 + 命令分隔符拦截**
  （;`&&`;`||`;`|`;换行;反引号;$() 一律拒绝，防「echo hi; rm -rf /」式串联逃逸）。
- 爆炸半径：容器内 appuser 无 sudo，最坏仅影响容器可写区；本沙箱进一步收窄到沙箱工作目录。
- **非 OS 级硬边界**：沙箱不提供 seccomp/namespace/chroot。受信任对话方（boss）使用，
  非多租户对抗场景。docstring 不宣称「无逃逸通道」——仅尽力收窄。

任何异常 → {ok:False, error}（诚实边界，不击穿 /chat）。
"""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

# 沙箱工作目录：项目根 / .chat_sandbox（与代码/配置目录隔离）。
_SANDBOX_DIR = Path(__file__).resolve().parent.parent / ".chat_sandbox"
_TIMEOUT = 15          # 单次执行超时（秒）
_MAX_OUTPUT = 4000     # 单流输出截断上限（字符）

# run_python 允许导入的模块白名单（仅安全、无副作用的标准库）。
_ALLOWED_MODULES = frozenset({
    "math", "json", "re", "datetime", "statistics", "collections", "itertools",
    "fractions", "decimal", "random", "string", "textwrap", "hashlib", "base64",
    "time", "calendar", "typing", "dataclasses", "heapq", "bisect", "array",
    "copy", "enum", "functools", "operator", "numbers",
})

# run_python 受限内置（去掉 os/subprocess/shutil/sys/文件类危险 builtins）。
_SAFE_BUILTINS = {
    "print": print, "len": len, "range": range, "str": str, "int": int, "float": float,
    "list": list, "dict": dict, "tuple": tuple, "set": set, "bool": bool,
    "sorted": sorted, "sum": sum, "min": min, "max": max, "abs": abs, "round": round,
    "enumerate": enumerate, "zip": zip, "map": map, "filter": filter, "any": any,
    "all": all, "reversed": reversed, "slice": slice, "format": format, "repr": repr,
    "isinstance": isinstance, "type": type, "Exception": Exception,
    "ValueError": ValueError, "TypeError": TypeError, "KeyError": KeyError,
    "IndexError": IndexError, "ZeroDivisionError": ZeroDivisionError,
    "RuntimeError": RuntimeError, "StopIteration": StopIteration, "next": next,
    "iter": iter, "getattr": getattr, "hasattr": hasattr, "callable": callable,
    "ord": ord, "chr": chr, "bin": bin, "hex": hex, "oct": oct, "divmod": divmod,
    "pow": pow, "complex": complex, "frozenset": frozenset, "bytes": bytes,
    "bytearray": bytearray, "memoryview": memoryview,
}

# run_shell 危险命令子串（大小写不敏感）—命中即拒绝执行。
_DANGEROUS = (
    "rm -rf", "rm -fr", "rm -r /", "rm -rf /", "rd /s", "rd /q",
    "del /f", "del /q", "deltree",
    "dd if=", "mkfs", "format ", "diskpart", "fsutil",
    "sudo", "su ", "runas",
    ":(){", "(){:|:&",                      # fork-bomb
    "shutdown", "reboot", "poweroff", "halt", "logoff",
    "chmod -r", "chmod -R", "chown -r", "chown -R", "takeown", "icacls",
    "curl ", "wget ", "certutil",           # 下载/执行
    "powershell", "pwsh", "cmd ", "cmd.exe", "/c ", "/k ",
    "python", "python3", "perl", "ruby", "node ", "node.exe",  # 防经解释器逃逸
    "reg ", "net ", "netsh", "taskkill", "schtasks", "sc ", "bcdedit", "vssadmin",
)
# 命令分隔符：禁止串联，防「echo hi ; rm -rf /」式绕过（见模块 docstring）。
_SEPARATORS = (";", "&&", "||", "|", "`", "$(", "\n", "\r")


def _ensure_dir() -> Path:
    try:
        _SANDBOX_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as e:  # noqa: BLE001
        logger.warning("沙箱目录创建失败 %s: %s", _SANDBOX_DIR, e)
    return _SANDBOX_DIR


def _trunc(text: str) -> str:
    if text is None:
        return ""
    text = text if isinstance(text, str) else str(text)
    if len(text) > _MAX_OUTPUT:
        return text[:_MAX_OUTPUT] + f"\n…(输出截断，共 {len(text)} 字符)"
    return text


def _is_dangerous(cmd: str) -> str | None:
    low = (cmd or "").lower()
    for sep in _SEPARATORS:
        if sep in cmd:
            return f"分隔符「{sep.strip() or '换行'}」"
    for pat in _DANGEROUS:
        if pat in low:
            return pat
    return None


# run_python 子进程引导脚本：隔离模式(-I)下装配受限命名空间再 exec 用户代码。
# 注意：_SAFE_BUILTINS 含函数对象、不可经 %r 往返，故只传「内置名元组」，由子进程
# 用 getattr(builtins, n) 重建；_ALLOWED_MODULES 为 frozenset（%r 合法）。
_PY_RUNNER = r"""
import sys, io, contextlib, builtins
_ALLOWED = %r
_SAFE_NAMES = %r
def _safe_import(name, *a, **k):
    top = name.split(".")[0]
    if top not in _ALLOWED:
        raise ImportError("沙箱禁止导入模块：" + name)
    return builtins.__import__(name, *a, **k)
_SAFE = {n: getattr(builtins, n) for n in _SAFE_NAMES}
# import 语句从 __builtins__ 字典里找 __import__，必须把受限导入器注进该字典，
# 否则报「__import__ not found」。
_SAFE["__import__"] = _safe_import
code = sys.stdin.read()
buf = io.StringIO()
try:
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        exec(compile(code, "<sandbox>", "exec"),
             {"__builtins__": _SAFE, "__import__": _safe_import})
except SystemExit:
    pass
except BaseException as e:
    sys.stderr.write(type(e).__name__ + ": " + str(e) + "\n")
sys.stdout.write(buf.getvalue())
""" % (_ALLOWED_MODULES, tuple(_SAFE_BUILTINS.keys()))


def run_python(code: str) -> Dict[str, Any]:
    """在受限子进程(-I)中以受限命名空间执行 Python（计算/解析，无系统/文件访问）。"""
    if not code or not code.strip():
        return {"ok": False, "error": "空代码", "stdout": "", "stderr": "", "exit_code": -1}
    workdir = _ensure_dir()
    # 强制子进程 UTF-8（Windows 默认 cp936 会让中文 stderr 在父进程 utf-8 解码时炸）。
    env = {"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-c", _PY_RUNNER],
            input=code,
            cwd=str(workdir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_TIMEOUT,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"执行超时（>{_TIMEOUT}s，已终止）",
                "stdout": "", "stderr": "", "exit_code": 124}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"执行异常: {e}", "stdout": "", "stderr": "", "exit_code": -1}
    # 子进程把异常写进 stderr、正常输出写进 stdout（见 _PY_RUNNER）；
    # 受限命名空间内代码无副作用，returncode==0 且 stderr 为空即视为成功。
    stderr = proc.stderr or ""
    return {
        "ok": proc.returncode == 0 and not stderr.strip(),
        "stdout": _trunc(proc.stdout),
        "stderr": _trunc(stderr),
        "exit_code": proc.returncode,
    }


def run_shell(command: str) -> Dict[str, Any]:
    """在沙箱中执行 shell 命令（危险命令/分隔符拦截 + 超时 + 截断）。"""
    if not command or not command.strip():
        return {"ok": False, "error": "空命令", "stdout": "", "stderr": "", "exit_code": -1}
    hit = _is_dangerous(command)
    if hit:
        return {"ok": False, "error": f"命令被沙箱拦截（命中「{hit}」）",
                "stdout": "", "stderr": "", "exit_code": 126}
    workdir = _ensure_dir()
    # 跨平台：容器（Linux）走 /bin/sh；本地/Windows 测试与运行时走 cmd.exe。
    shell_cmd = ["cmd.exe", "/c", command] if sys.platform.startswith("win") \
        else ["/bin/sh", "-c", command]
    env = {"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    try:
        proc = subprocess.run(
            shell_cmd,
            cwd=str(workdir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_TIMEOUT,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"执行超时（>{_TIMEOUT}s，已终止）",
                "stdout": "", "stderr": "", "exit_code": 124}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"执行异常: {e}", "stdout": "", "stderr": "", "exit_code": -1}
    return {
        "ok": True,
        "stdout": _trunc(proc.stdout),
        "stderr": _trunc(proc.stderr),
        "exit_code": proc.returncode,
    }
