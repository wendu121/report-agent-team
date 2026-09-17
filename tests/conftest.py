# tests/conftest.py · 测试期统一绑定多租户上下文
#
# 背景（为什么必须有这个文件）：
#   多租户改造后，所有可配置资源路径改走 server/tenancy.py，且契约是 **fail loud**——
#   没有账号上下文就抛 TenancyError，绝不静默回落全局根（静默回落 = 假隔离）。
#   但大批改造前写的测试（test_orchestrator / test_regression_edge / test_m12_experts /
#   test_m11_3_synthesis / test_llm_drop_params::loader …）并未绑定账号，
#   一调到 load_model_mapping / _load_custom_providers 就崩，表现为 25 failed + 15 errors。
#
# 处置（拒绝两种偷懒做法）：
#   ❌ 不开 RAT_LEGACY_GLOBAL=1 —— 那是官方标注「假隔离、Phase 2 前必须删」的逃生阀，
#      拿它跑测试等于用假隔离掩盖真问题。
#   ❌ 不去逐个改 43 条老测试 —— 改动面大且与本任务无关。
#   ✅ 统一注入测试账号：租户根指向 tmp（不污染真实 tenants/）+ 从仓库 BASE 播种配置类资源，
#      既保留真实隔离语义，又让老测试读到与改造前等价的配置内容。

import pytest

from server import tenancy

TEST_ACCOUNT = "test-account"


@pytest.fixture(autouse=True)
def tenant_context(tmp_path, monkeypatch):
    """每个测试自动拥有独立租户根（tmp）+ 已绑定的账号上下文。"""
    monkeypatch.setattr(tenancy, "TENANTS_ROOT", tmp_path / "tenants")
    tenancy.set_current_account(TEST_ACCOUNT)
    # 从仓库默认配置播种（只拷 config/agents/gates/templates/skills/experts，
    # 不含 .secrets/.audit/outputs/.engine_state —— 密钥不继承）
    tenancy.ensure_account_layout(TEST_ACCOUNT, seed_from_base=True)
    yield
    tenancy.reset_current_account()
