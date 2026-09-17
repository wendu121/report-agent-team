"""test_converge_gate.py · Converge Gate 集成测试

验证 converge_gate.py 与 edict-gate 门禁的集成逻辑。
"""
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def mock_state(tmp_path):
    """创建 mock engine state。"""
    state_dir = tmp_path / ".engine_state"
    state_dir.mkdir()
    state = {
        "user_task": {
            "topic": "AI 芯片市场研报",
            "scope": ["供给", "需求", "竞争格局"],
            "output_format_spec": "markdown",
            "constraints": ["中文输出"],
        },
        "retrieval_records": [
            {"id": "rec-1", "url": "https://a.com/1", "title": "供给分析",
             "snippet": "供给端产能吃紧。", "credibility": "high"},
            {"id": "rec-2", "url": "https://a.com/2", "title": "需求分析",
             "snippet": "需求旺盛增长。", "credibility": "high"},
        ],
        "analysis_conclusions": [
            {"id": "con-1", "claim": "供给紧张", "source_ids": ["rec-1"], "confidence": "high"},
            {"id": "con-2", "claim": "需求旺盛", "source_ids": ["rec-2"], "confidence": "high"},
        ],
        "draft_segments": [
            {"id": "seg-1", "section": "供给", "content": "供给端产能吃紧。", "conclusion_ids": ["con-1"]},
            {"id": "seg-2", "section": "需求", "content": "需求旺盛。", "conclusion_ids": ["con-2"]},
        ],
        "report_markdown": "# AI 芯片市场研报\n\n## 供给\n供给端产能吃紧。\n\n## 需求\n需求旺盛。\n\n## 引用/来源\n- [rec-1](https://a.com/1)\n- [rec-2](https://a.com/2)\n",
        "routing_state": {"round": 1},
        "gap_tasks": [],
    }
    (state_dir / "state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return state, tmp_path


@pytest.fixture
def mock_state_with_gaps(tmp_path):
    """创建有 gap 的 mock state。"""
    state_dir = tmp_path / ".engine_state"
    state_dir.mkdir()
    state = {
        "user_task": {
            "topic": "AI 芯片市场研报",
            "scope": ["供给", "需求", "竞争格局"],  # 缺少"竞争格局"覆盖
            "output_format_spec": "markdown",
            "constraints": ["中文输出"],
        },
        "retrieval_records": [
            {"id": "rec-1", "url": "https://a.com/1", "title": "供给分析",
             "snippet": "供给端产能吃紧。", "credibility": "high"},
        ],
        "analysis_conclusions": [
            {"id": "con-1", "claim": "供给紧张", "source_ids": ["rec-99"], "confidence": "high"},  # 无效引用
        ],
        "draft_segments": [
            {"id": "seg-1", "section": "供给", "content": "供给端产能吃紧。", "conclusion_ids": ["con-1"]},
        ],
        "report_markdown": "# AI 芯片市场研报\n\n## 供给\n供给端产能吃紧。\n",  # 缺引用章节
        "routing_state": {"round": 1},
        "gap_tasks": [],
    }
    (state_dir / "state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return state, tmp_path


class TestConvergeGateScript:
    """converge_gate.py 脚本测试。"""

    def test_load_state(self, mock_state):
        """测试 state 加载。"""
        from scripts.converge_gate import load_state
        _, tmp_path = mock_state
        # 模拟环境变量
        os.environ["CONVERGE_STATE_PATH"] = str(tmp_path / ".engine_state" / "state.json")
        loaded = load_state()
        assert loaded is not None
        assert loaded["user_task"]["topic"] == "AI 芯片市场研报"

    def test_classify_gaps(self, mock_state):
        """测试 gap 分类。"""
        from scripts.converge_gate import classify_gaps
        gaps = [
            {"id": "gap-A-1", "type": "coverage", "description": "子问题 'X' 无覆盖"},
            {"id": "gap-B-1", "type": "broken_reference", "description": "引用不存在"},
            {"id": "gap-C-1", "type": "missing_section", "description": "缺章节"},
            {"id": "gap-C-2", "type": "incomplete_citation", "description": "引用不完整"},
        ]
        closed = {"gap-C-1"}  # 已关闭
        classified = classify_gaps(gaps, closed)
        assert len(classified["blocking"]) == 2
        assert len(classified["warnings"]) == 1  # gap-C-1 已关闭，但 gap-C-2 仍在

    def test_append_to_tasks(self, tmp_path):
        """测试 tasks.md 追加。"""
        from scripts.converge_gate import append_to_tasks, TASKS_PATH
        # 临时替换 TASKS_PATH
        original = TASKS_PATH
        try:
            tasks_file = tmp_path / "tasks.md"
            import scripts.converge_gate as cg
            cg.TASKS_PATH = tasks_file
            gaps = [
                {"id": "gap-A-1", "type": "coverage", "description": "子问题 'X' 无覆盖",
                 "spec_ref": "user_task.scope[0]", "suggested_action": "补充检索"},
            ]
            append_to_tasks(gaps, round_num=1)
            content = tasks_file.read_text(encoding="utf-8")
            assert "gap-A-1" in content
            assert "[ ]" in content  # 未关闭
        finally:
            cg.TASKS_PATH = original

    def test_update_review_with_converge(self, tmp_path):
        """测试 REVIEW.md 更新。"""
        from scripts.converge_gate import update_review_with_converge, REVIEW_PATH
        original = REVIEW_PATH
        try:
            review_file = tmp_path / "REVIEW.md"
            import scripts.converge_gate as cg
            cg.REVIEW_PATH = review_file
            blocking = [{"id": "gap-A-1", "type": "coverage", "description": "子问题 'X' 无覆盖"}]
            warnings = [{"id": "gap-C-1", "type": "missing_section", "description": "缺章节"}]
            update_review_with_converge(blocking, warnings)
            content = review_file.read_text(encoding="utf-8")
            assert "Converge 漂移检测" in content
            assert "Blocking gaps: 1" in content
            assert "Warning gaps: 1" in content
        finally:
            cg.REVIEW_PATH = original


class TestConvergeGateIntegration:
    """与 edict-gate 门禁的集成测试。"""

    def test_gate_integration_flow(self, mock_state_with_gaps):
        """测试完整门禁流程：converge 检查 → 分类 → 封驳判定。"""
        from scripts.converge_gate import (
            load_state,
            run_converge_check,
            classify_gaps,
            load_closed_gaps,
        )
        state, tmp_path = mock_state_with_gaps
        os.environ["CONVERGE_STATE_PATH"] = str(tmp_path / ".engine_state" / "state.json")

        # 加载 state
        loaded_state = load_state()
        assert loaded_state is not None

        # 运行 converge 检查
        gaps = run_converge_check(loaded_state, round_num=1)
        assert len(gaps) > 0

        # 加载已关闭的 gaps
        closed = load_closed_gaps()
        assert isinstance(closed, set)

        # 分类
        classified = classify_gaps(gaps, closed)
        # 应该有 blocking gaps（coverage + broken_reference）
        assert len(classified["blocking"]) > 0

    def test_no_state_graceful_exit(self, tmp_path):
        """测试无 state 时的优雅退出。"""
        from scripts.converge_gate import load_state
        os.environ.pop("CONVERGE_STATE_PATH", None)
        state = load_state()
        assert state is None


class TestGapClassification:
    """gap 严重度分类测试。"""

    def test_blocking_types(self):
        """测试 blocking 类型判定。"""
        from scripts.converge_gate import BLOCKING_TYPES, WARNING_TYPES
        assert "coverage" in BLOCKING_TYPES
        assert "broken_reference" in BLOCKING_TYPES
        assert "missing_section" in WARNING_TYPES
        assert "incomplete_citation" in WARNING_TYPES

    def test_classify_empty_gaps(self):
        """测试空 gaps 列表。"""
        from scripts.converge_gate import classify_gaps
        classified = classify_gaps([], set())
        assert classified["blocking"] == []
        assert classified["warnings"] == []
