"""
审计包生成器
M6-5 产出：ZIP 打包逻辑（report.md + gate_review_history.json + prior_versions.json + user_task.json + engine_events.json）
对齐 API_SPEC.md §5
"""

import zipfile
import json
import io
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime

from .models import Task


# ============================================================================
# 审计包生成
# ============================================================================


_GATE_FIELDS = ("decision", "reason", "eval_score", "problem_points", "gate", "round")
_EVENT_FIELDS = ("event", "agent", "gate", "reason", "round")


def _as_dt(v):
    """字符串时间戳 → datetime；已是 datetime 原样返回；解析失败置 None（不抛）。"""
    if not v:
        return None
    if isinstance(v, datetime):
        return v
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001 —— 审计导出不应因一条脏时间而整个失败
        return None


def _shadow(obj, fields):
    """把 pydantic 的 gate/event 投影成生成器期望的形状（含 datetime 化时间戳）。"""
    from types import SimpleNamespace
    data = {f: getattr(obj, f, None) for f in fields}
    data["timestamp"] = _as_dt(getattr(obj, "timestamp", None))
    return SimpleNamespace(**data)


class _ApiTaskView:
    """把 API 的 TaskResponse 适配成生成器期望的 server.models.Task 形状。

    为什么需要这个类：生成器当初按 **DB 模型**写（task.gate_reviews / task.topic / task.scope …），
    而服务端点 `_owned_task` 返回的是 **TaskResponse**（pydantic：gate 记录在
    `routing_state.gate_review_history`、主题在 `user_task.topic`）。两侧形状不一致，
    正是 `/audit-export` 一直只返回 TODO 临时 JSON、生成器沦为死代码的原因。

    适配原则：**不改生成器**（它仍服务 DB 模型），只在入口做一层只读视图。
    status 统一转成字符串值——metadata.json 要 json.dumps，枚举会 TypeError。
    """

    def __init__(self, t):
        rs = getattr(t, "routing_state", None)
        ut = getattr(t, "user_task", None)
        self.task_id = t.task_id
        self.status = getattr(getattr(t, "status", None), "value", getattr(t, "status", None))
        self.created_at = getattr(t, "created_at", None)
        self.updated_at = getattr(t, "updated_at", None)
        self.report_markdown = getattr(t, "report_markdown", None) or ""
        # 生成器对 gate/event 调 `timestamp.isoformat()`（DB 模型是 DateTime 列），
        # 但 TaskResponse 里 timestamp 是**字符串** → 直接传会 AttributeError。
        # 故逐条投影成 SimpleNamespace 并把时间还原成 datetime（解析失败置 None，不抛）。
        self.gate_reviews = [_shadow(g, _GATE_FIELDS)
                             for g in (getattr(rs, "gate_review_history", None) or [])]
        self.engine_events = [_shadow(e, _EVENT_FIELDS)
                              for e in (getattr(rs, "engine_events", None) or [])]
        self.prior_versions = getattr(t, "prior_versions", None) or {}
        self.retrieval_records = list(getattr(t, "retrieval_records", None) or [])
        self.analysis_conclusions = list(getattr(t, "analysis_conclusions", None) or [])
        self.draft_segments = list(getattr(t, "draft_segments", None) or [])
        self.topic = getattr(ut, "topic", None) or ""
        self.scope = list(getattr(ut, "scope", None) or [])
        self.output_format_spec = getattr(ut, "output_format_spec", None) or ""
        self.constraints = list(getattr(ut, "constraints", None) or [])


class AuditExportGenerator:
    """审计包生成器"""

    def __init__(self, task: Task):
        self.task = task
        self.timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")

    @classmethod
    def from_api_task(cls, task) -> "AuditExportGenerator":
        """从 API 的 TaskResponse 构造（端点侧入口）。"""
        return cls(_ApiTaskView(task))

    def generate_zip(self) -> bytes:
        """生成审计包 ZIP（二进制）"""
        zip_buffer = io.BytesIO()

        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            # 1. report.md
            if self.task.report_markdown:
                zip_file.writestr(
                    f"report.md",
                    self.task.report_markdown.encode("utf-8")
                )

            # 2. gate_review_history.json
            gate_review_history = [
                {
                    "decision": gr.decision,
                    "reason": gr.reason,
                    "eval_score": gr.eval_score,
                    "problem_points": gr.problem_points or [],
                    "gate": gr.gate,
                    "round": gr.round,
                    "timestamp": gr.timestamp.isoformat() if gr.timestamp else None
                }
                for gr in self.task.gate_reviews
            ]
            zip_file.writestr(
                f"gate_review_history.json",
                json.dumps(gate_review_history, ensure_ascii=False, indent=2)
            )

            # 3. prior_versions.json
            zip_file.writestr(
                f"prior_versions.json",
                json.dumps(self.task.prior_versions or {}, ensure_ascii=False, indent=2)
            )

            # 4. user_task.json
            user_task = {
                "topic": self.task.topic,
                "scope": self.task.scope or [],
                "output_format_spec": self.task.output_format_spec,
                "constraints": self.task.constraints or []
            }
            zip_file.writestr(
                f"user_task.json",
                json.dumps(user_task, ensure_ascii=False, indent=2)
            )

            # 5. engine_events.json
            engine_events = [
                {
                    "event": ee.event,
                    "agent": ee.agent,
                    "gate": ee.gate,
                    "reason": ee.reason,
                    "round": ee.round,
                    "timestamp": ee.timestamp.isoformat() if ee.timestamp else None
                }
                for ee in self.task.engine_events
            ]
            zip_file.writestr(
                f"engine_events.json",
                json.dumps(engine_events, ensure_ascii=False, indent=2)
            )

            # 6. metadata.json（审计包元数据）
            metadata = {
                "task_id": self.task.task_id,
                "status": self.task.status,
                "created_at": self.task.created_at.isoformat() if self.task.created_at else None,
                "updated_at": self.task.updated_at.isoformat() if self.task.updated_at else None,
                "exported_at": datetime.utcnow().isoformat(),
                "total_gate_reviews": len(self.task.gate_reviews),
                "total_engine_events": len(self.task.engine_events),
                "total_retrieval_records": len(self.task.retrieval_records or []),
                "total_analysis_conclusions": len(self.task.analysis_conclusions or []),
                "total_draft_segments": len(self.task.draft_segments or [])
            }
            zip_file.writestr(
                f"metadata.json",
                json.dumps(metadata, ensure_ascii=False, indent=2)
            )

        zip_buffer.seek(0)
        return zip_buffer.read()

    def get_filename(self) -> str:
        """获取审计包文件名"""
        return f"audit-export-{self.task.task_id}-{self.timestamp}.zip"


# ============================================================================
# 审计包保存到磁盘
# ============================================================================


def save_audit_package(task: Task, output_dir: Path) -> Path:
    """保存审计包到磁盘"""
    generator = AuditExportGenerator(task)
    zip_data = generator.generate_zip()
    zip_path = output_dir / generator.get_filename()

    output_dir.mkdir(parents=True, exist_ok=True)

    with open(zip_path, "wb") as f:
        f.write(zip_data)

    print(f"✅ 审计包已保存: {zip_path}")
    return zip_path


# ============================================================================
# 审计包加载（用于测试）
# ============================================================================


def load_audit_package(zip_path: Path) -> Dict[str, Any]:
    """加载审计包（返回所有文件内容）"""
    result = {}

    with zipfile.ZipFile(zip_path, 'r') as zip_file:
        for filename in zip_file.namelist():
            with zip_file.open(filename) as file:
                content = file.read()

                if filename.endswith('.json'):
                    result[filename] = json.loads(content.decode('utf-8'))
                elif filename.endswith('.md'):
                    result[filename] = content.decode('utf-8')
                else:
                    result[filename] = content

    return result


# ============================================================================
# 审计包验证
# ============================================================================


def validate_audit_package(zip_path: Path) -> bool:
    """验证审计包完整性"""
    required_files = [
        "report.md",
        "gate_review_history.json",
        "prior_versions.json",
        "user_task.json",
        "engine_events.json",
        "metadata.json"
    ]

    with zipfile.ZipFile(zip_path, 'r') as zip_file:
        filenames = zip_file.namelist()

        for required in required_files:
            if required not in filenames:
                print(f"❌ 审计包缺少必需文件: {required}")
                return False

        # 验证 metadata.json
        with zip_file.open("metadata.json") as f:
            metadata = json.load(f.read())
            if "task_id" not in metadata or "exported_at" not in metadata:
                print("❌ metadata.json 格式错误")
                return False

    print("✅ 审计包验证通过")
    return True