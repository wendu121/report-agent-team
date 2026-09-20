"""test_skill_condense.py · _condense_markdown + install_spec 裁剪路径与可调用性。

目标：把完整 public-apis 级（~259KB）markdown 经 install_spec 落盘成 ≤5KB 精简索引版，
且通过 build_skill_context 证明 chat / researcher 两角色都能读到注入标记（保留可调用性）。

隔离策略：monkeypatch tools.skill_importer._tenant_root 与 tools.skills._tenant_root
都指向临时根，使 install_spec 写出的 skills/<id>.md 与 config/skills.yaml 落在临时根，
且 build_skill_context 从同一临时根回读 —— 不污染真实 tenants/，也不依赖 conftest 播种。
"""
import types

import pytest

from tools import skill_importer
from tools.skills import build_skill_context, load_skills


SID = "apilayer-unified-suite"


def _make_big_markdown() -> str:
    """构造一个 ~259KB 级的 mock markdown：banner 图 / CTA / 50 条 API 列表 / 产品 URL / 冗余填充。"""
    parts = []
    # 1) banner 图片行（裸 raw.githubusercontent 图片）
    parts.append(
        "![APILayer Banner](https://raw.githubusercontent.com/apilayer/apilayer/master/"
        "apilayer-banner.png)"
    )
    parts.append("")
    # 2) 营销 CTA 行（含 Sign up / postman.com / app.apilayer.com / emoji）
    parts.append("Sign up for free at https://apilayer.com to get your API key today!")
    parts.append("Read the docs on postman.com and 🎉 join our community 🥳")
    parts.append("Manage keys at https://app.apilayer.com/dashboard")
    parts.append("")
    # 3) 50 条命名 API 列表项（含 http，应被抽进索引，封顶 40）
    for i in range(50):
        name = f"API{i:02d}"
        parts.append(f"- [{name}](https://apilayer.com/products/{name.lower()}-api)")
    # 4) 若干 api.apilayer.com 产品 URL（应保留为索引，不被 CTA 过滤误删）
    parts.append("- https://api.apilayer.com/products/weather-api")
    parts.append("- https://api.apilayer.com/products/currency-api")
    parts.append("")
    # 5) 冗余填充正文（无 http，用于把体积撑到 ~259KB，且不应进入索引）
    filler = "占位说明：本段为原始 README 的冗余正文，用于撑大体积以验证裁剪效果。"
    target = 259 * 1024
    n = target // len(filler) + 5
    parts.append(filler * n)
    return "\n".join(parts)


def _make_spec(markdown: str) -> dict:
    return {
        "id": SID,
        "level": "L1",
        "name": "APILayer 统一套件",
        "description": "统一 API 套件：聚合多家公共数据 API，覆盖 IP、汇率、天气等领域。",
        "target": "skill",
        "target_roles": ["chat", "researcher"],
        "payload": {"markdown": markdown},
        "provenance": {
            "source_url": "https://github.com/apilayer/apilayer",
            "source_type": "github",
            "sha256": "deadbeef",
        },
        "reasons": ["含外部 URL"],
    }


def _count_index_entries(text: str) -> int:
    """统计“可用 API 索引”区块下的列表项条数。"""
    lines = text.splitlines()
    in_index = False
    count = 0
    for line in lines:
        if line.strip().startswith("## 可用 API 索引"):
            in_index = True
            continue
        if in_index:
            if line.strip().startswith("## "):  # 进入下一个区块
                break
            if re_match_index(line):
                count += 1
    return count


def re_match_index(line: str) -> bool:
    return bool(line.strip().startswith("- [") and "](" in line)


# --------------------------------------------------------------------------
# 单元测试：_condense_markdown 的过滤 / 去重 / 封顶 / 头部结构
# --------------------------------------------------------------------------
def test_condense_filters_and_caps():
    raw = "\n".join([
        "![Banner](https://raw.githubusercontent.com/x/y/banner.png)",
        "Sign up now at postman.com and 🎉 celebrate 🥳",
        "Manage at https://app.apilayer.com/dash",
        "# 原始标题（应被忽略，改用 spec name）",
        "- [Weather](https://api.apilayer.com/products/weather-api)",
        "- [Currency](https://api.apilayer.com/products/currency-api)",
        "- [Weather](https://api.apilayer.com/products/weather-api)",  # 重复 → 去重
        "- https://apilayer.com/products/ip-api",
        "正文段落（无 http，不应进索引）",
    ])
    spec = {"name": "测试技能", "description": "一个用于单测的精简索引技能，描述略长但会被截断到一百二十字以内以避免超出头部预算限制。"}
    out = skill_importer._condense_markdown(raw, spec)

    assert "可用 API 索引" in out
    assert "调用方式" in out
    # 营销噪音被剔除
    assert "raw.githubusercontent.com/x/y/banner" not in out
    assert "Sign up" not in out
    assert "postman.com" not in out
    assert "app.apilayer.com" not in out
    assert "🎉" not in out
    assert "🥳" not in out
    # 产品 URL 被保留（api.apilayer.com 不是 CTA）
    assert "api.apilayer.com/products/weather-api" in out
    assert "apilayer.com/products/ip-api" in out
    # 去重：Weather 仅 1 条
    assert out.count("weather-api") == 1
    # 头部用 spec name 而非原始 # 标题
    assert out.startswith("# 测试技能")
    # 原文 >5KB 才加注，这里原文远小于 5KB → 不应有裁剪注
    assert "原始来源已裁剪" not in out


def test_condense_empty_raw_returns_placeholder():
    out = skill_importer._condense_markdown("", {"name": "空技能"})
    assert "没有提供正文内容" in out
    assert "空技能" in out


# --------------------------------------------------------------------------
# 端到端：install_spec 落盘裁剪版 + build_skill_context 可调用性
# --------------------------------------------------------------------------
def test_install_spec_writes_condensed_and_callable(tmp_path, monkeypatch):
    root = tmp_path / "tenant_root"
    root.mkdir(parents=True, exist_ok=True)

    # 让 importer 与 skills 两套 _tenant_root 都指向临时根
    monkeypatch.setattr(skill_importer, "_tenant_root", lambda: root)
    monkeypatch.setattr("tools.skills._tenant_root", lambda: root)

    markdown = _make_big_markdown()
    assert len(markdown) > 250_000  # 确认确实是大体积 mock

    spec = _make_spec(markdown)
    res = skill_importer.install_spec(spec, operator="test")
    assert res["status"] == "installed"
    assert res["id"] == SID

    md_path = root / "skills" / f"{SID}.md"
    yaml_path = root / "config" / "skills.yaml"
    assert md_path.exists(), "skills/<sid>.md 应已写出"
    assert yaml_path.exists(), "config/skills.yaml 应已写出"

    text = md_path.read_text(encoding="utf-8")
    # 尺寸与内容断言
    assert len(text) < 8000, f"裁剪后 md 应 < 8000 字符，实际 {len(text)}"
    assert "raw.githubusercontent.com" not in text, "banner 图片不应残留"
    assert "可用 API 索引" in text
    assert "调用方式" in text
    entries = _count_index_entries(text)
    assert 0 < entries <= 40, f"API 索引条数应在 (0,40]，实际 {entries}"

    # skills.yaml 已登记该 entry 且 enabled=True
    import yaml as _yaml

    data = _yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    entries_yaml = [s for s in (data.get("skills") or []) if isinstance(s, dict)]
    matched = [s for s in entries_yaml if s.get("id") == SID]
    assert matched, "skills.yaml 应包含该 entry"
    assert matched[0].get("enabled") is True
    assert matched[0].get("target_roles") == ["chat", "researcher"]

    # 可调用性：两个角色都能读到注入标记（说明 md 被真实拼入 system prompt）
    chat_ctx = build_skill_context("chat")
    research_ctx = build_skill_context("researcher")
    assert "【已启用技能：" in chat_ctx, "chat 角色应注入该技能"
    assert "【已启用技能：" in research_ctx, "researcher 角色应注入该技能"
    # 加载到的技能里包含该 id
    assert any(s.get("id") == SID for s in load_skills())


# --------------------------------------------------------------------------
# 回归（NOTE-3.3）：L0 直装路径复用 install_spec，自研小 prompt 技能不得被裁成空壳
# --------------------------------------------------------------------------
def test_install_spec_preserves_small_prompt_skill(tmp_path, monkeypatch):
    """自研 L0 技能：正文是步骤/结构指令、几乎无 http 列表项、体积 < 5KB。

    守卫 `_should_condense` 应判为 False → 原样落盘，prompt 内容全部保留。
    """
    root = tmp_path / "tenant_root"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(skill_importer, "_tenant_root", lambda: root)
    monkeypatch.setattr("tools.skills._tenant_root", lambda: root)

    # 一段典型自研研报框架 prompt（无 http 列表项、< 5KB）
    prompt = (
        "# 竞品分析框架\n\n"
        "## 分析步骤\n"
        "1. 明确对比维度（价格 / 功能 / 市场定位）\n"
        "2. 收集各竞品公开资料与用户评价\n"
        "3. 按维度打分并汇总差异\n\n"
        "## 输出要求\n"
        "- 用表格呈现对比\n"
        "- 结论给出明确推荐\n"
    )
    assert len(prompt) < 5000

    spec = {
        "id": "competitor-framework",
        "level": "L0",
        "name": "竞品分析框架",
        "description": "自研竞品分析 prompt 框架。",
        "target": "skill",
        "target_roles": ["researcher", "analyst"],
        "payload": {"markdown": prompt},
        "provenance": {"source_url": "local", "source_type": "local", "sha256": "abc"},
        "reasons": [],
    }
    res = skill_importer.install_spec(spec, operator="test")
    assert res["status"] == "installed"

    md_path = root / "skills" / "competitor-framework.md"
    text = md_path.read_text(encoding="utf-8")
    # 守卫生效：原样写，不生成空索引骨架
    assert "可用 API 索引" not in text, "小 prompt 技能不应被裁出 API 索引骨架"
    assert "竞品分析框架" in text
    assert "分析步骤" in text and "输出要求" in text, "prompt 正文应完整保留"
    # 注入标记仍可被对应角色读到（可调用性不丢）
    assert "【已启用技能：" in build_skill_context("researcher")
    assert "【已启用技能：" in build_skill_context("analyst")


# --------------------------------------------------------------------------
# 回归（V6.1）：幂等必须按 (id, source_url, sha256) 收敛，不能只按 (source_url, sha256)。
# 否则「内容相同但 id 不同」的两个技能被误判重复 → 接受第二个提案时静默 no-op、
# 返回 unchanged、却把提案删除、技能始终不落盘（审批卡显示成功、实际零落盘）。
# --------------------------------------------------------------------------
def test_idempotent_scoped_per_id(tmp_path, monkeypatch):
    root = tmp_path / "tenant_root"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(skill_importer, "_tenant_root", lambda: root)

    prov = {"source_url": "https://github.com/apilayer/apilayer", "sha256": "deadbeef"}
    spec_a = {
        "id": "skill-a",
        "level": "L1",
        "name": "技能A",
        "description": "d",
        "target": "skill",
        "target_roles": ["chat"],
        "payload": {"markdown": "# A\n\n正文"},
        "provenance": prov,
    }
    res = skill_importer.install_spec(spec_a, operator="test")
    assert res["status"] == "installed"

    # 同 content 但不同 id=B → 不能误判为重复（修复前会返回 True，吃掉第二个提案）
    spec_b = dict(spec_a, id="skill-b", name="技能B")
    assert skill_importer._idempotent(spec_b) is False, \
        "不同 id 的同源同内容技能不应被判为重复（否则审批会静默丢提案）"

    # 同 id=A 同 content → 幂等 True（可安全重复接受，不重复落盘）
    assert skill_importer._idempotent(dict(spec_a)) is True

