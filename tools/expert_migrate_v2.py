"""expert_migrate_v2.py · Expert Manifest v1→v2 迁移工具

借鉴 spec-kit Bundle manifest schema，把现有专家包升级到 v2。
迁移后 plugin.json 含 requires/provides/catalog 字段，
兼容原有 read_expert() 路径（未知字段忽略）。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE = Path(__file__).resolve().parent.parent
EXPERT_DIR = BASE / "experts"

# 安全 pattern：只允许 kebab-case slug
_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?$")

# categoryId → shape 启发式（与 tools/experts.py 的 CATEGORY_SHAPE 保持一致）
_CATEGORY_SHAPE = {
    "01-ProductDesign": "analyst",
    "02-Engineering": "researcher",
    "03-DataAnalytics": "analyst",
    "04-ContentWriting": "writer",
    "05-DesignCreative": "writer",
    "06-Marketing": "writer",
    "07-Sales": "writer",
    "08-HumanResources": "analyst",
    "09-FinanceAccounting": "analyst",
    "10-LegalCompliance": "analyst",
    "11-EducationTraining": "researcher",
    "12-IndustryConsultant": "analyst",
}


def _slugify(s: str) -> str:
    s = re.sub(r"[^\w\u4e00-\u9fa5-]+", "-", (s or "").strip().lower())
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "imported-expert"


def _valid_slug(s: str) -> bool:
    return bool(_SLUG_RE.match(s))


def migrate_package(pkg_dir: Path) -> Dict[str, Any]:
    """迁移单个专家包到 v2 schema，原地更新 plugin.json。

    返回：{"id": ..., "old_version": ..., "new_version": ..., "warnings": [...]}
    """
    pj_path = pkg_dir / "plugin.json"
    if not pj_path.exists():
        raise FileNotFoundError(f"plugin.json 不存在：{pkg_dir}")

    with pj_path.open("r", encoding="utf-8") as f:
        plugin: Dict[str, Any] = json.load(f)

    eid = plugin.get("name") or _slugify(pkg_dir.name)
    warnings: List[str] = []

    # 1. schema_version
    plugin["schema_version"] = "2.0"

    # 2. id 规范化（若缺失或非法）
    if not eid or not _valid_slug(eid):
        new_id = _slugify(eid or pkg_dir.name)
        warnings.append(f"id 非法或缺失，已规范化为 {new_id!r}")
        plugin["name"] = new_id
        eid = new_id

    # 3. version 默认 1.0.0（v1 无 version 时）
    if not plugin.get("version"):
        plugin["version"] = "1.0.0"
        warnings.append("version 缺失，补默认 1.0.0")

    # 4. requires
    if "requires" not in plugin:
        plugin["requires"] = {
            "workbuddy_version": ">=1.0.0",
            "connectors": [],
            "mcp": [],
        }
        warnings.append("requires 缺失，补空声明")

    # 5. provides
    if "provides" not in plugin:
        agent_name = plugin.get("agentName") or eid
        agents_list = [
            {
                "id": agent_name,
                "prompt_file": f"agents/{agent_name}.md",
                "priority": 10,
            }
        ]
        plugin["provides"] = {
            "agents": agents_list,
            "workflows": [],
            "presets": [],
        }
        warnings.append("provides 缺失，补默认 agents 条目")

    # 6. catalog
    if "catalog" not in plugin:
        plugin["catalog"] = {
            "source": "",
            "pin_version": plugin["version"],
            "offline_policy": "install-allowed",
            "integrity": {
                "manifest_sha256": "",
                "files": [],
            },
        }
        warnings.append("catalog 缺失，补空声明")

    # 7. 确保 agents 列表有 prompt_file 路径合法
    for ag in (plugin.get("provides") or {}).get("agents") or []:
        pf = ag.get("prompt_file", "")
        if pf and not pf.startswith("agents/"):
            warnings.append(f"agent {ag.get('id')} 的 prompt_file {pf!r} 不在 agents/ 下")

    # 8. 备份原文件
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_dir = BASE / ".audit" / "expert_backups" / stamp
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_pj = backup_dir / f"{eid}-plugin.json.bak"
    if pj_path.exists():
        backup_pj.write_text(
            pj_path.read_text(encoding="utf-8"), encoding="utf-8"
        )

    # 9. 写回（原子写）
    tmp = pj_path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(plugin, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp.replace(pj_path)

    return {"id": eid, "warnings": warnings}


def migrate_all() -> List[Dict[str, Any]]:
    """扫描 experts/ 下所有子目录，逐包迁移，返回结果列表。"""
    results = []
    if not EXPERT_DIR.exists():
        return results
    for child in sorted(EXPERT_DIR.iterdir()):
        if not child.is_dir():
            continue
        if child.name.startswith("."):
            continue
        try:
            r = migrate_package(child)
            r["status"] = "ok"
        except Exception as e:
            r = {"id": child.name, "status": "error", "error": str(e)}
        results.append(r)
    return results


if __name__ == "__main__":
    results = migrate_all()
    print(json.dumps(results, ensure_ascii=False, indent=2))
