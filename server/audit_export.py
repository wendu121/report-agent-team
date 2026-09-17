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


class AuditExportGenerator:
    """审计包生成器"""

    def __init__(self, task: Task):
        self.task = task
        self.timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")

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