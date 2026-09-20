"""
引擎 CLI 封装
M6-3 产出：引擎进程启动 / 状态查询 / 事件推送

架构（对齐 API_SPEC.md §1）：
- 每任务一个独立引擎进程（隔离 state）
- 通过文件系统通信（input.json → output.json）
- 通过回调函数推送 WebSocket 事件
"""

import subprocess
import asyncio
import json
import sys
import os
from pathlib import Path
from typing import Optional, Callable, Any
from datetime import datetime


# ============================================================================
# 引擎进程封装
# ============================================================================


class EngineProcess:
    """引擎进程封装（每任务一进程）"""

    def __init__(
        self,
        task_id: str,
        user_task: dict,
        state_dir: Path,
        event_callback: Optional[Callable[[str, dict], Any]] = None,
        max_rounds: int = 2,
        agents: Optional[list] = None,
        plugins: Optional[list] = None,
        model: Optional[str] = None,
        gate_model: Optional[str] = None,  # 方案 A：审核模型(Gate)覆盖
        account_id: Optional[str] = None,   # 多租户：透传给子进程以绑定租户根
    ):
        self.task_id = task_id
        self.user_task = user_task
        self.account_id = account_id
        # 多租户：有 account_id 时状态目录按账号隔离（覆盖传入的全局 state_dir）。
        if account_id:
            try:
                from server import tenancy
                state_dir = tenancy.tenant_path(".engine_state")
            except Exception:
                pass
        self.state_dir = state_dir
        self.event_callback = event_callback
        self.max_rounds = max_rounds
        self.agents = agents
        self.plugins = plugins
        self.model = model
        self.gate_model = gate_model

        self.input_file = state_dir / f"{task_id}_input.json"
        self.output_file = state_dir / f"{task_id}_output.json"
        self.process: Optional[subprocess.Popen] = None

    async def start(self):
        """启动引擎进程"""
        print(f"🚀 启动引擎进程: task_id={self.task_id}")

        # 写入输入文件
        input_data = {
            "task_id": self.task_id,
            "user_task": self.user_task,
            "max_rounds": self.max_rounds,
            "agents": self.agents or [],
            # M9-5 / ChatEntry 控制项：本次任务的数据源 + 模型接口（真·控制器透传）。
            "plugins": self.plugins or [],
            "model": self.model,
            "gate_model": self.gate_model,  # 方案 A：审核模型透传
            "output_file": str(self.output_file)
        }
        # 多租户：透传 account_id，子进程据此 bind_account + 设 RAT_ACCOUNT_ID 读自己的资源
        if self.account_id:
            input_data["account_id"] = self.account_id

        # 状态目录必须存在再写：多租户改造后 state_dir 会被改写成
        # {TENANTS_ROOT}/<account_id>/.engine_state（见 __init__ 与 launch_engine），
        # 而该目录此前没有任何地方创建（RESOURCE_DIRS 也没有它）→ 写文件直接
        # FileNotFoundError，任务「创建成功」却在 5ms 内 escalated，用户永远拿不到报告。
        # 这里是最靠近写入点的一层兜底，对「老账号目录已存在但缺 .engine_state」也生效。
        # 依据：VERIFICATION_ENGINE_STATE_DIR.md（2026-09-19 实机事故复盘）
        self.state_dir.mkdir(parents=True, exist_ok=True)

        with open(self.input_file, "w", encoding="utf-8") as f:
            json.dump(input_data, f, ensure_ascii=False, indent=2)

        # 启动进程（调用 engine_runner.py）
        # TD-006 修复：原代码硬编码宿主 Windows 解释器绝对路径，Linux 容器内
        # subprocess.Popen 必抛 [Errno 2] No such file or directory → 任务创建后
        # 数毫秒内 escalated。改用 sys.executable：
        #   宿主 = 当前运行的解释器；容器 = /usr/local/bin/python（实测值）。
        # 跨平台自适应，且保证子进程与父进程同一环境（依赖一致）。
        python_path = sys.executable
        # 多租户：把 account_id 经环境变量透传给子进程（子进程 ContextVar 不跨进程，
        # 必须靠 RAT_ACCOUNT_ID 让 orchestrator 等模块解析到正确租户根）。
        sub_env = dict(os.environ)
        if self.account_id:
            sub_env["RAT_ACCOUNT_ID"] = self.account_id
        self.process = subprocess.Popen(
            [
                python_path,
                "-m", "server.engine_runner",
                str(self.input_file)
            ],
            cwd=Path(__file__).parent.parent,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=sub_env
        )

        print(f"✅ 引擎进程已启动: PID={self.process.pid}")

    async def wait(self) -> dict:
        """等待引擎进程完成"""
        if self.process is None:
            raise RuntimeError("引擎进程未启动")

        # 等待进程结束
        await asyncio.get_event_loop().run_in_executor(
            None, self.process.wait
        )

        # 读取输出文件
        if self.output_file.exists():
            with open(self.output_file, "r", encoding="utf-8") as f:
                return json.load(f)
        else:
            return {"error": "输出文件不存在"}

    def is_alive(self) -> bool:
        """检查进程是否存活"""
        return self.process is not None and self.process.poll() is None

    async def terminate(self):
        """终止引擎进程"""
        if self.process and self.is_alive():
            self.process.terminate()
            await asyncio.get_event_loop().run_in_executor(
                None, self.process.wait
            )
            print(f"🛑 引擎进程已终止: task_id={self.task_id}")

    async def get_output(self) -> Optional[dict]:
        """获取引擎输出（非阻塞）"""
        if self.output_file.exists():
            with open(self.output_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return None


# ============================================================================
# 引擎事件推送（WebSocket 回调）
# ============================================================================


class EngineEventEmitter:
    """引擎事件发射器（延迟导入避免循环依赖）"""
    
    @staticmethod
    async def emit(task_id: str, event: dict):
        """推送引擎事件到 WebSocket（回调函数）"""
        try:
            from .websocket import manager
            await manager.broadcast(task_id, event)
            print(f"📤 推送事件: task_id={task_id}, event_type={event.get('event_type')}")
        except ImportError:
            print(f"⚠️ WebSocket 模块未加载，事件推送失败: {event}")


async def emit_engine_event(task_id: str, event: dict):
    """推送引擎事件到 WebSocket（回调函数）"""
    await EngineEventEmitter.emit(task_id, event)


# ============================================================================
# 引擎进程管理器（服务层使用）
# ============================================================================


class EngineProcessManager:
    """引擎进程管理器（服务层全局单例）"""

    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.processes: dict[str, EngineProcess] = {}

    async def launch_engine(
        self,
        task_id: str,
        user_task: dict,
        max_rounds: int = 2,
        agents: Optional[list] = None,
        plugins: Optional[list] = None,
        model: Optional[str] = None,
        gate_model: Optional[str] = None,  # 方案 A：审核模型(Gate)覆盖
        account_id: Optional[str] = None,   # 多租户：透传租户根给子进程
    ) -> EngineProcess:
        """启动引擎进程"""
        # 多租户：有 account_id 时状态目录按账号隔离（覆盖管理器初始化时的全局目录）。
        state_dir = self.state_dir
        if account_id:
            try:
                from server import tenancy
                state_dir = tenancy.tenant_path(".engine_state")
            except Exception:
                pass
        process = EngineProcess(
            task_id=task_id,
            user_task=user_task,
            state_dir=state_dir,
            event_callback=emit_engine_event,
            max_rounds=max_rounds,
            agents=agents,
            plugins=plugins,
            model=model,
            gate_model=gate_model,
            account_id=account_id
        )

        await process.start()
        self.processes[task_id] = process

        return process

    async def get_engine_process(self, task_id: str) -> Optional[EngineProcess]:
        """获取引擎进程"""
        return self.processes.get(task_id)

    async def get_engine_output(self, task_id: str) -> Optional[dict]:
        """获取引擎输出"""
        if task_id in self.processes:
            return await self.processes[task_id].get_output()
        return None

    async def terminate_engine(self, task_id: str):
        """终止引擎进程"""
        if task_id in self.processes:
            await self.processes[task_id].terminate()
            del self.processes[task_id]

    async def cleanup(self):
        """清理所有引擎进程"""
        for task_id, process in list(self.processes.items()):
            await process.terminate()
        self.processes.clear()


# ============================================================================
# 状态存储（临时实现：文件系统）
# ============================================================================


class EngineStateStore:
    """引擎状态存储（临时实现：文件系统）"""

    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def save_state(self, task_id: str, state: dict):
        """保存状态"""
        state_file = self.state_dir / f"{task_id}.json"
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

    def load_state(self, task_id: str) -> Optional[dict]:
        """加载状态"""
        state_file = self.state_dir / f"{task_id}.json"
        if state_file.exists():
            with open(state_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

    def delete_state(self, task_id: str):
        """删除状态"""
        state_file = self.state_dir / f"{task_id}.json"
        if state_file.exists():
            state_file.unlink()