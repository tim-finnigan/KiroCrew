"""An ``mcp.json`` server must reach new sessions on a refused shared agent home.

Shape under test: an instance on a non-default ``KIROCREW_HOME`` shares
``~/.kiro/agents`` with a default-home install. ``_decline_shared_agent_home``
refuses every spec rebuild from it, which is correct -- but the primary spec pins
``includeMcpJson: false``, so the rebuild is the ONLY way a server added to
``~/.kiro/settings/mcp.json`` reaches a session, while the gateway's probe starts
the server from the source file and shows it Online. A gateway stub reaches the
session regardless, because the overlay is injected at ``session/new`` from this
instance's own data home.

The refused rebuild projects this instance's own servers privately, and the
session path delivers them at ``session/new``.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

#: An absolute command that exists on every CI OS, so command resolution keeps it.
_CMD = sys.executable


@pytest.fixture(autouse=True)
def _pin_default_home(monkeypatch, tmp_path):
    """Keep the guard's default-home comparison and breadcrumb off the real home."""
    from kiro_crew.config import paths

    monkeypatch.setattr(paths, "_resolved_home", None, raising=False)
    monkeypatch.setattr(paths, "_resolve_default_home", lambda: tmp_path / "pinned-default-home")
    monkeypatch.setattr(paths, "_write_recovery_breadcrumb", lambda data_home: None, raising=False)
    monkeypatch.delenv("KIRO_HOME", raising=False)
    monkeypatch.delenv("KIROCREW_POD", raising=False)


@pytest.fixture(autouse=True)
def _fresh_projection():
    """The projection is process memory; no test may see another's."""
    from kiro_crew import mcp_declined_home

    mcp_declined_home.clear_projection()
    yield
    mcp_declined_home.clear_projection()


def _shared_agents(monkeypatch, agents_dir: Path) -> None:
    """Make *agents_dir* both the write target and the ambient (shared) agents dir."""
    from kiro_crew import agent

    monkeypatch.setattr(agent, "KIRO_AGENTS_DIR", agents_dir)
    monkeypatch.setattr(agent, "ambient_agents_dir", lambda: agents_dir)


def _durable_checkout(monkeypatch) -> None:
    from kiro_crew import agent

    monkeypatch.setattr(agent, "__file__", "/durable-install/KiroCrew/src/kiro_crew/agent.py")


def _write_spec(agents_dir: Path, spec: dict) -> None:
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / "kirocrew.json").write_text(json.dumps(spec), encoding="utf-8")


#: What a DEFAULT-home install leaves in the shared agents dir: managed entries
#: pinned to no ``KIROCREW_HOME`` (the default-home writer's signature).
_DEFAULT_HOME_SPEC = {
    "name": "kirocrew",
    "includeMcpJson": False,
    "mcpServers": {
        "kirocrew-core": {"command": "kirocrew", "args": ["mcp", "core"], "env": {}},
        "taskei": {"command": "taskei-mcp"},
    },
    "tools": ["@kirocrew-core", "@taskei"],
}


def _declined_home(monkeypatch, tmp_path, settings_servers: dict) -> Path:
    """A non-default home beside a default-home spec, with *settings_servers*.

    Returns the shared agents dir. Nothing is faked on the decision path: the
    real guard refuses because the shared spec carries the default-home signature.
    """
    from kiro_crew import agent

    monkeypatch.setenv("KIROCREW_HOME", str(tmp_path / "relocated-home"))
    _durable_checkout(monkeypatch)
    shared = tmp_path / "agents"
    _write_spec(shared, _DEFAULT_HOME_SPEC)
    _shared_agents(monkeypatch, shared)
    settings = tmp_path / "settings-mcp.json"
    settings.write_text(json.dumps({"mcpServers": settings_servers}), encoding="utf-8")
    monkeypatch.setattr(agent, "_KIRO_MCP_JSON", settings)
    return shared


def _kiro_session_array(tmp_path, stubs=()) -> list[dict]:
    """What a kiro ``session/new`` receives in ``mcpServers`` (the shared append)."""
    from kiro_crew.acp.client import AcpClient

    client = AcpClient(work_dir=tmp_path / "work", agent="kirocrew")
    client._pooled_broker_stubs = lambda: [dict(s) for s in stubs]  # type: ignore[method-assign]
    return client._pooled_mcp_servers()


class TestDeclinedAgentHomeDelivery:
    def test_mcp_json_server_reaches_session_new_through_the_real_decline(
        self, monkeypatch, tmp_path
    ):
        """The reported shape, end to end: refused rebuild -> session/new array.

        Reverted (no projection on the refused rebuild, or no append on the
        session path), ``outlook`` is absent from what kiro-cli receives -- the
        Online-but-no-tools report.
        """
        from kiro_crew import agent

        shared = _declined_home(
            monkeypatch,
            tmp_path,
            {
                "outlook": {"command": _CMD, "args": ["--mcp"], "autoApprove": ["*"]},
                "taskei": {"command": _CMD},
                "muted": {"command": _CMD, "disabled": True},
                "remote": {"url": "https://example.invalid/mcp"},
            },
        )

        _path, wrote = agent.rebuild_agent_config_reporting()

        assert wrote is False, "the default-home install's shared spec must not be written"
        spec = json.loads((shared / "kirocrew.json").read_text(encoding="utf-8"))
        assert spec == _DEFAULT_HOME_SPEC, "the other install's spec must stay unchanged"

        by_name = {e["name"]: e for e in _kiro_session_array(tmp_path)}
        # taskei: already in the shared spec. muted: disabled. remote: not stdio.
        assert set(by_name) == {"outlook"}, by_name
        outlook = by_name["outlook"]
        assert os.path.normcase(outlook["command"]) == os.path.normcase(_CMD)
        assert outlook["args"] == ["--mcp"]
        # No grant rides along, and no session credential reaches a third party.
        assert "autoApprove" not in outlook
        assert all("TOKEN" not in pair["name"] for pair in outlook["env"])

    def test_a_stubbed_name_is_not_registered_twice(self, monkeypatch, tmp_path):
        from kiro_crew import agent

        _declined_home(monkeypatch, tmp_path, {"outlook": {"command": _CMD}})
        agent.rebuild_agent_config()
        stub = {"name": "outlook", "command": "/stub", "args": [], "env": [], "type": "stdio"}

        array = _kiro_session_array(tmp_path, stubs=[stub])

        assert [e["name"] for e in array] == ["outlook"]
        assert array[0]["command"] == "/stub"

    def test_nothing_is_delivered_once_the_home_owns_its_spec(self, monkeypatch, tmp_path):
        """A written spec mounts the server itself; the projection is retired."""
        from kiro_crew import agent

        _declined_home(monkeypatch, tmp_path, {"outlook": {"command": _CMD}})
        agent.rebuild_agent_config()
        assert [e["name"] for e in _kiro_session_array(tmp_path)] == ["outlook"]

        # The other install's specs are gone: this home now writes its own.
        for spec in (tmp_path / "agents").glob("kirocrew*.json"):
            spec.unlink()
        _path, wrote = agent.rebuild_agent_config_reporting()

        assert wrote is True
        assert _kiro_session_array(tmp_path) == []

    def test_no_file_in_the_data_home_decides_what_a_session_launches(
        self, monkeypatch, tmp_path
    ):
        """Delivery is recomputed from the sources, never read back from disk.

        A writable cache in the data home would let its writer pick the next
        session's command; the session must see only what ``mcp.json`` says.
        """
        from kiro_crew import agent
        from kiro_crew.config.paths import config_dir

        _declined_home(monkeypatch, tmp_path, {"outlook": {"command": _CMD, "args": ["ok"]}})
        before = set(Path(config_dir()).rglob("*")) if Path(config_dir()).exists() else set()
        agent.rebuild_agent_config()
        after = set(Path(config_dir()).rglob("*")) if Path(config_dir()).exists() else set()
        assert not [p for p in after - before if "mcp" in p.name], after - before

        # A planted file with the old projection's name changes nothing.
        planted = Path(config_dir()) / "declined-home-mcp.json"
        planted.parent.mkdir(parents=True, exist_ok=True)
        planted.write_text(
            json.dumps({"servers": {"outlook": {"command": "/evil", "args": []}}}),
            encoding="utf-8",
        )
        array = _kiro_session_array(tmp_path)
        assert [(e["name"], e["args"]) for e in array] == [("outlook", ["ok"])]
        assert os.path.normcase(array[0]["command"]) == os.path.normcase(_CMD)

    def test_an_entry_past_a_bound_is_refused_whole_and_the_count_is_capped(
        self, monkeypatch, tmp_path
    ):
        from kiro_crew import agent, mcp_declined_home

        monkeypatch.setattr(mcp_declined_home, "MAX_DELIVERED_SERVERS", 2)
        servers = {
            "huge-arg": {"command": _CMD, "args": ["x" * (mcp_declined_home.MAX_FIELD_CHARS + 1)]},
            "many-args": {"command": _CMD, "args": ["a"] * (mcp_declined_home.MAX_SERVER_ARGS + 1)},
            "huge-env": {"command": _CMD, "env": {"K": "v" * (mcp_declined_home.MAX_FIELD_CHARS + 1)}},
            "s1": {"command": _CMD, "env": {"A": "1"}, "timeout": 5, "extra": {"deep": ["x"]}},
            "s2": {"command": _CMD},
            "s3": {"command": _CMD},
        }
        _declined_home(monkeypatch, tmp_path, servers)
        agent.rebuild_agent_config()

        array = _kiro_session_array(tmp_path)
        assert [e["name"] for e in array] == ["s1", "s2"]
        # Only the launch fields are retained -- in the stored projection, not
        # just in the element built from it.
        assert set(array[0]) == {"name", "command", "args", "env"}
        assert array[0]["env"] == [{"name": "A", "value": "1"}]
        stored = mcp_declined_home.refresh_projection()
        assert sorted(stored) == ["s1", "s2"]
        assert all(set(entry) == {"command", "args", "env"} for entry in stored.values())

    def test_a_collision_suffixed_mount_is_still_delivered(self, monkeypatch, tmp_path):
        """``foo/bar`` and ``foo-bar`` share an alias; the rebuild suffixes one."""
        from kiro_crew import agent

        _declined_home(
            monkeypatch,
            tmp_path,
            {
                "foo/bar": {"command": _CMD, "args": ["slash"]},
                "foo-bar": {"command": _CMD, "args": ["dash"]},
            },
        )
        agent.rebuild_agent_config()

        array = _kiro_session_array(tmp_path)
        assert sorted(e["args"][0] for e in array) == ["dash", "slash"], array
        assert len({e["name"] for e in array}) == 2

    @pytest.mark.asyncio
    async def test_kas_is_not_shadowed_by_a_checkout_agent(self, monkeypatch, tmp_path):
        """KAS reads the user-level spec alone, so no work_dir shadow check applies."""
        from kiro_crew import mcp_declined_home
        from kiro_crew.acp.runtime import AcpRuntime
        from kiro_crew.acp_backends import ACP_BACKEND_KAS, ACP_BACKEND_KIRO

        seen: list[object] = []

        def capture(agent, *, work_dir=None, present=()):
            seen.append(work_dir)
            return []

        monkeypatch.setattr(mcp_declined_home, "session_servers", capture)
        fake = SimpleNamespace(acp_backend=ACP_BACKEND_KAS, _agent="kirocrew")
        await AcpRuntime._with_declined_home_servers(fake, [], None, tmp_path)
        fake.acp_backend = ACP_BACKEND_KIRO
        await AcpRuntime._with_declined_home_servers(fake, [], None, tmp_path)

        assert seen == [None, tmp_path]

    def test_registry_mode_delivers_nothing(self, monkeypatch, tmp_path):
        from kiro_crew import agent

        _declined_home(monkeypatch, tmp_path, {"outlook": {"command": _CMD}})
        agent.rebuild_agent_config()
        monkeypatch.setattr(agent, "_mcp_registry_mode", lambda: True)

        assert _kiro_session_array(tmp_path) == []

    def test_an_unchanged_source_is_not_recomputed_and_an_edit_is(self, monkeypatch, tmp_path):
        """The refresh poll re-runs the refused rebuild; it must not redo the passes."""
        from kiro_crew import agent, mcp_declined_home

        _declined_home(monkeypatch, tmp_path, {"outlook": {"command": _CMD}})
        calls: list[int] = []
        real = mcp_declined_home.compute_projection

        def counting():
            calls.append(1)
            return real()

        monkeypatch.setattr(mcp_declined_home, "compute_projection", counting)
        agent.rebuild_agent_config()
        agent.rebuild_agent_config()
        assert len(calls) == 1

        settings = tmp_path / "settings-mcp.json"
        settings.write_text(
            json.dumps({"mcpServers": {"calendar": {"command": _CMD, "args": ["x"]}}}),
            encoding="utf-8",
        )
        agent.rebuild_agent_config()
        assert len(calls) == 2
        assert [e["name"] for e in _kiro_session_array(tmp_path)] == ["calendar"]

    def test_another_agent_gets_nothing(self, monkeypatch, tmp_path):
        from kiro_crew import agent, mcp_declined_home

        _declined_home(monkeypatch, tmp_path, {"outlook": {"command": _CMD}})
        agent.rebuild_agent_config()

        assert mcp_declined_home.session_servers("kirocrew-worker") == []

    @pytest.mark.asyncio
    async def test_the_shared_runtime_appends_after_the_stubs(self, monkeypatch, tmp_path):
        """AcpRuntime (kiro and KAS) takes the same delivery; mirrors do not."""
        from kiro_crew import agent
        from kiro_crew.acp.runtime import AcpRuntime
        from kiro_crew.acp_backends import ACP_BACKEND_CLAUDE, ACP_BACKEND_KIRO

        _declined_home(monkeypatch, tmp_path, {"outlook": {"command": _CMD}})
        agent.rebuild_agent_config()
        stubs = [{"name": "kirocrew-core", "command": "/stub", "args": [], "env": []}]

        fake = SimpleNamespace(acp_backend=ACP_BACKEND_KIRO, _agent="kirocrew")
        out = await AcpRuntime._with_declined_home_servers(fake, stubs, None, tmp_path)
        assert [e["name"] for e in out] == ["kirocrew-core", "outlook"]

        fake.acp_backend = ACP_BACKEND_CLAUDE
        assert await AcpRuntime._with_declined_home_servers(fake, stubs, None, tmp_path) == stubs
