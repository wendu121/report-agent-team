"""LibreChat 借鉴：NewApiLLMClient 的 dropParams（静态剔除 + 一次性自愈）。

全部 hermetic：注入假 client 接管 NewApiLLMClient._get_client，零网络。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator import NewApiLLMClient, LLMError  # noqa: E402


class _FakeMsg:
    content = '{"ok": true}'
    tool_calls = None


class _FakeChoice:
    message = _FakeMsg()


class _FakeResp:
    choices = [_FakeChoice()]


class _Completions:
    def __init__(self, rec):
        self._rec = rec

    def create(self, **kwargs):
        return self._rec._create(**kwargs)


class _ChatNS:
    def __init__(self, rec):
        self.completions = _Completions(rec)


class FakeClient:
    """假 OpenAI client：记录每次 create() 的 kwargs，可按需按序抛错。"""

    def __init__(self, errors=None):
        self.errors = list(errors or [])
        self.calls = []
        self.chat = _ChatNS(self)

    def _create(self, **kwargs):
        self.calls.append(dict(kwargs))
        if self.errors:
            e = self.errors.pop(0)
            if e is not None:
                raise e
        return _FakeResp()


@pytest.fixture
def patch_time(monkeypatch):
    """自愈路径会走到 time.sleep 兜底分支？不会，但我们仍禁用真实睡眠以防万一。"""
    slept = []
    monkeypatch.setattr("time.sleep", lambda s: slept.append(s))
    return slept


def _mk(monkeypatch, recorder, **iface_extra):
    endpoints = [{"id": "gw", "base_url": "https://gw/v1", "api_key": "k",
                  "source": "builtin"}]
    interfaces = [dict({"id": "auto-chat", "endpoint": "gw",
                        "model_id": "real-model"}, **iface_extra)]
    c = NewApiLLMClient(endpoints=endpoints, interfaces=interfaces,
                        default_endpoint_id="gw", timeout=9)
    monkeypatch.setattr(c, "_get_client", lambda eid: recorder)
    return c


def test_no_drop_by_default(monkeypatch, patch_time):
    """未配置 ⇒ 与改动前完全一致：temperature 照发，timeout 照传。"""
    rec = FakeClient()
    c = _mk(monkeypatch, rec)
    c.complete("auto-chat", "sys", "usr")
    assert len(rec.calls) == 1
    kw = rec.calls[0]
    assert kw["temperature"] == 0.2
    assert kw["model"] == "real-model"
    assert kw["timeout"] == 9          # SDK 级选项，绝不能被剔除
    assert kw["messages"][0]["role"] == "system"


def test_interface_level_drop(monkeypatch, patch_time):
    rec = FakeClient()
    c = _mk(monkeypatch, rec, drop_params=["temperature"])
    c.complete("auto-chat", "sys", "usr")
    kw = rec.calls[0]
    assert "temperature" not in kw
    assert kw["model"] == "real-model" and "messages" in kw
    assert kw["timeout"] == 9


def test_endpoint_level_fallback(monkeypatch, patch_time):
    """interface 未配 ⇒ 回落到 endpoint 级（判空必须让 [] 透出，否则此路是死代码）。"""
    rec = FakeClient()
    c = _mk(monkeypatch, rec)  # interface 的 drop_params 会是 []
    c._endpoints["gw"]["drop_params"] = ["temperature"]
    c.complete("auto-chat", "sys", "usr")
    assert "temperature" not in rec.calls[0]


def test_env_var_fallback(monkeypatch, patch_time):
    rec = FakeClient()
    c = _mk(monkeypatch, rec)
    c._endpoints["gw"]["drop_params"] = []
    monkeypatch.setenv("LLM_DROP_PARAMS", "temperature")
    c.complete("auto-chat", "sys", "usr")
    assert "temperature" not in rec.calls[0]


def test_interface_wins_over_endpoint(monkeypatch, patch_time):
    rec = FakeClient()
    c = _mk(monkeypatch, rec, drop_params=["TEMPERATURE"])  # 大小写不敏感
    c._endpoints["gw"]["drop_params"] = ["nothing"]
    c.complete("auto-chat", "sys", "usr")
    assert "temperature" not in rec.calls[0]


def test_heal_on_unsupported_param(monkeypatch, patch_time):
    """上游 400 说不支持 temperature ⇒ 自动剔除后重试一次并成功。"""
    bad = RuntimeError("BadRequestError: unsupported parameter: 'temperature'")
    rec = FakeClient(errors=[bad])
    c = _mk(monkeypatch, rec)
    assert c.complete("auto-chat", "sys", "usr") == '{"ok": true}'
    assert len(rec.calls) == 2
    assert "temperature" in rec.calls[0]
    assert "temperature" not in rec.calls[1]
    assert patch_time == []  # 发生了自愈重试，不该走退避睡眠


def test_heal_bounded_to_once(monkeypatch, patch_time):
    """持续报错 ⇒ 自愈只生效一次，随后退避并如实失败，绝不被拖进无限重试。"""
    bad = RuntimeError("400 unsupported parameter: 'temperature'")
    rec = FakeClient(errors=[bad, bad, bad])
    c = NewApiLLMClient(
        endpoints=[{"id": "gw", "base_url": "https://gw/v1", "api_key": "k"}],
        interfaces=[{"id": "auto-chat", "endpoint": "gw", "model_id": "m"}],
        default_endpoint_id="gw", max_retries=2)
    monkeypatch.setattr(c, "_get_client", lambda eid: rec)
    with pytest.raises(LLMError):
        c.complete("auto-chat", "sys", "usr")
    # 自愈后 temperature 必须**永久**缺席，说明一次剔除生效且未被重复处理
    assert all("temperature" not in k for k in rec.calls[1:])


def test_heal_never_drops_tools(monkeypatch, patch_time):
    """绝不自愈剔除 tools：那等于把有工具依据的调用悄悄降成凭空作答（制造幻觉）。"""
    bad = RuntimeError("400 unsupported parameter: 'tools'")
    rec = FakeClient(errors=[bad, bad, bad])  # 持续报错 ⇒ 调用最终必须失败
    c = _mk(monkeypatch, rec)
    with pytest.raises(LLMError):
        c.complete_with_tools("auto-chat", "sys", "usr", tools=[{"name": "x"}])
    assert len(rec.calls) == 3  # 耗尽 retries
    assert all("tools" in k for k in rec.calls)  # 但一次都没被剔除
    assert len(patch_time) == 2  # 走的是退避重试，而非自愈（自愈不该发生）


def test_fc_static_drop_still_allowed(monkeypatch, patch_time):
    """tools 可以被**显式配置**剔除（人的选择，且会看到缺失语义的后果）。"""
    rec = FakeClient()
    c = _mk(monkeypatch, rec, drop_params=["tools", "tool_choice"])
    c.complete_with_tools("auto-chat", "sys", "usr", tools=[{"name": "x"}])
    assert "tools" not in rec.calls[0] and "tool_choice" not in rec.calls[0]


def test_loader_preserves_drop_params():
    """bingo loader 白名单必须透传 drop_params —— 否则配置文件写了也是"假配置"。"""
    from orchestrator import load_model_interfaces_and_endpoints
    import tempfile
    yaml_text = """
endpoints:
  - id: gw
    base_url: https://gw/v1
    api_key: k
    drop_params: ["temperature"]
interfaces:
  - id: auto-chat
    endpoint: gw
    model_id: m
    drop_params: ["stop"]
default_endpoint: gw
"""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "models.yaml"
        p.write_text(yaml_text, encoding="utf-8")
        eps, ifaces, _gw, default = load_model_interfaces_and_endpoints(str(p))
        assert default == "gw"
        assert eps[0]["drop_params"] == ["temperature"]
        assert ifaces[0]["drop_params"] == ["stop"]
