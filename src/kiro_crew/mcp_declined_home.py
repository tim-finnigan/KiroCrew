"""Session-level delivery of ``mcp.json`` servers when the shared spec is refused.

The primary agent spec pins ``includeMcpJson: false``, so the spec rebuild is
what turns an ``mcp.json`` entry into a mounted server: it copies the entry into
``mcpServers`` and its ``@ref`` into ``tools``. An instance on a non-default
``KIROCREW_HOME`` that shares ``~/.kiro/agents`` with another install is refused
that rebuild (:func:`kiro_crew.agent._decline_shared_agent_home`), correctly --
the spec belongs to the other install and pins ITS home into every managed
entry. Without this module the refused instance's own ``mcp.json`` servers then
reach none of its sessions, while its dashboard probe shows them Online.

The refused rebuild therefore still runs the MCP half of the rebuild, in memory,
over this instance's own sources: the same merge, command resolution, alias
normalization and mute/disable handling a written spec would get
(the rebuild's own MCP passes, reached through :mod:`kiro_crew.agent`). What a written spec would
have mounted for a user-installed stdio server, and the shared spec does not, is
held in this process's memory -- never in the shared agents dir, and never in a
file a session start would trust. Each new primary session recomputes it when
the sources moved and appends those entries to its
``session/new`` array, the same session-level channel the gateway's broker
stubs ride. Nothing about the other install changes.

What is deliberately NOT carried:

* **Grants.** ``autoApprove`` is dropped and nothing is added to
  ``allowedTools``: the shared spec is not ours to widen, so every call to a
  delivered server goes through the approval gate. Mounting is not approving.
* **Remote (url) servers.** Only a stdio element is known to be accepted on the
  session-level channel; a remote server is named in the gateway log instead.
* **Registry mode.** An injected, unmarked entry is dropped by the client under
  a registry ceiling, and nothing here can resolve a name against the catalog,
  so nothing is delivered (the same ceiling the broker stubs observe).
* **Anything the session already has.** A name the shared spec declares, or a
  broker stub carries, is never delivered twice.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Collection
from pathlib import Path
from typing import Any

from kiro_crew.agent_files import AGENT_FILENAME
from kiro_crew.agent_sdk.mcp_refs import parse_tools_refs
from kiro_crew.mcp_cleanup import mcp_entry_is_muted, mcp_entry_is_registry_governed
from kiro_crew.mcp_utils import mcp_server_alias

logger = logging.getLogger(__name__)

#: Most servers one projection delivers. Every bound below shares this module's
#: one population (the delivered ``mcp.json`` entries), so they live together.
MAX_DELIVERED_SERVERS = 64
#: Most ``args`` / ``env`` entries one delivered server may carry.
MAX_SERVER_ARGS = 128
MAX_SERVER_ENV = 128
#: Longest command, argument, env name or env value retained, in characters.
MAX_FIELD_CHARS = 4096

PRIMARY_AGENT = Path(AGENT_FILENAME).stem

#: The last projection this process computed, keyed by the inputs it was
#: computed from. Process memory only: a file anyone in the data home can write
#: would let that writer choose the next session's launched command.
_MEMO: dict[str, Any] = {"fingerprint": None, "servers": {}}
_MEMO_LOCK = threading.Lock()


def compute_projection() -> dict[str, dict[str, Any]]:
    """What a written primary spec would mount for this instance's own servers.

    Runs the rebuild's MCP passes over an EMPTY base, so the result holds only
    what the sources contribute, resolved and normalized exactly as a written
    spec would hold it. Returns ``{alias: entry}`` for the user-installed stdio
    servers that pass every launch predicate; the shared spec is not consulted
    here (the session-side filter does that, against the file as it is then).
    """
    from kiro_crew import agent as agent_mod  # circular at module scope

    mcp_sources = agent_mod.mcp_sources
    mcp_aliases = agent_mod.mcp_aliases
    config: dict[str, Any] = {"mcpServers": {}, "tools": [], "allowedTools": []}
    sources = mcp_sources.merge_mcp_sources(config)
    resolved = mcp_sources.resolve_mcp_servers(config, sources, agent_mod._resolve_mcp_command)
    mounted = mcp_aliases.normalize_server_keys(config, resolved.unresolved)
    mcp_sources.sync_shared_server_refs(config, sources, mounted)

    user_aliases = _user_mount_aliases(sources, mounted)
    grant_all, refs = parse_tools_refs(config.get("tools"))
    referenced = set(refs)
    out: dict[str, dict[str, Any]] = {}
    remote: list[str] = []
    oversized: list[str] = []
    overflow: list[str] = []
    for alias, entry in sorted((config.get("mcpServers") or {}).items()):
        if alias not in user_aliases or not isinstance(entry, dict):
            continue
        if not (grant_all or alias in referenced):
            continue
        if mcp_entry_is_muted(entry) or mcp_entry_is_registry_governed(entry):
            continue
        if entry.get("url"):
            remote.append(alias)
            continue
        if not isinstance(entry.get("command"), str):
            continue
        bounded = _bounded_entry(entry)
        if bounded is None or len(alias) > MAX_FIELD_CHARS:
            oversized.append(alias[:64])
            continue
        if len(out) >= MAX_DELIVERED_SERVERS:
            overflow.append(alias)
            continue
        out[alias] = bounded
    if remote:
        logger.warning(
            "Shared agent home is not writable from this data home; remote MCP "
            "server(s) %s cannot be delivered at session level and will not reach "
            "this instance's sessions",
            ", ".join(sorted(remote)),
        )
    if oversized:
        logger.warning(
            "Not delivering %d mcp.json server(s) at session level: a command, "
            "argument or env entry exceeds the session-level bounds (%d entries, "
            "%d characters): %s",
            len(oversized),
            MAX_SERVER_ARGS,
            MAX_FIELD_CHARS,
            ", ".join(sorted(oversized)),
        )
    if overflow:
        logger.warning(
            "Delivering only the first %d mcp.json servers at session level; %d "
            "more were not delivered: %s",
            MAX_DELIVERED_SERVERS,
            len(overflow),
            ", ".join(overflow[:16]) + (" ..." if len(overflow) > 16 else ""),
        )
    return out


def _user_mount_aliases(sources: Any, mounted: dict[str, str]) -> set[str]:
    """Concrete mount aliases of the servers a user installed through ``mcp.json``.

    Taken from the rebuild's own source-to-alias map, so a server the alias pass
    mounted under a collision suffix (``name-2``) is still recognised as the
    user's.
    """
    managed = set(sources.managed_names) | {mcp_server_alias(n) for n in sources.managed_names}
    aliases: set[str] = set()
    for _label, scope in sources.scopes:
        if not isinstance(scope, dict):
            continue
        for raw in scope:
            name = str(raw)
            if name in managed or mcp_server_alias(name) in managed:
                continue
            aliases.add(mounted.get(name) or mcp_server_alias(name))
    return aliases


def _bounded_str(value: Any) -> str | None:
    if not isinstance(value, str):
        value = json.dumps(value, sort_keys=True, default=str)
    return value if len(value) <= MAX_FIELD_CHARS else None


def _bounded_entry(entry: dict[str, Any]) -> dict[str, Any] | None:
    """The launch fields of *entry*, bounded, or ``None`` when any exceeds a bound.

    Only ``command``, ``args`` and ``env`` are retained: they are all an ACP
    stdio element carries, and keeping nothing else means no grant
    (``autoApprove``) or bookkeeping key can ride along. An entry past a bound
    is refused whole rather than truncated, since a cut argument would launch a
    different command than the one the user configured.
    """
    command = _bounded_str(entry.get("command"))
    if not command:
        return None
    raw_args = entry.get("args")
    args_in = raw_args if isinstance(raw_args, (list, tuple)) else ()
    if len(args_in) > MAX_SERVER_ARGS:
        return None
    args: list[str] = []
    for item in args_in:
        bounded = _bounded_str(item)
        if bounded is None:
            return None
        args.append(bounded)
    raw_env = entry.get("env")
    env_in = raw_env if isinstance(raw_env, dict) else {}
    if len(env_in) > MAX_SERVER_ENV:
        return None
    env: dict[str, str] = {}
    for key, value in env_in.items():
        name = _bounded_str(str(key))
        val = _bounded_str(value if isinstance(value, str) else str(value))
        if name is None or val is None:
            return None
        env[name] = val
    return {"command": command, "args": args, "env": env}


def _source_fingerprint() -> list[Any]:
    """What the projection was computed from, cheaply: each source file's stat
    signature plus the search ``PATH``.

    A refused instance re-attempts the rebuild on every refresh poll, and the
    projection runs the full MCP passes (command resolution, audit events). An
    unchanged fingerprint skips them; any edit to an ``mcp.json`` scope, or a
    ``PATH`` change that can move command resolution, recomputes.
    """
    import os

    from kiro_crew import agent as agent_mod  # circular at module scope

    files = [agent_mod._KIRO_MCP_JSON, agent_mod._user_dir() / "mcp.json"]
    files.extend(agent_mod.mcp_sources._extra_mcp_scope_globals())
    sig: list[Any] = []
    for path in files:
        try:
            st = Path(path).stat()
            sig.append([str(path), st.st_mtime_ns, st.st_size])
        except OSError:
            sig.append([str(path), None, None])
    sig.append(os.environ.get("PATH", ""))
    return sig


def refresh_projection() -> dict[str, dict[str, Any]]:
    """The projection for the current sources, recomputed only when they moved.

    Called from the refused rebuild (boot, a Sync, a sessions restart, a
    dashboard MCP change, the refresh poll) and from every session start, so a
    session always sees what the sources say NOW. The result lives in process
    memory only and is recomputed from the protected ``mcp.json`` sources the
    written spec would itself be built from; nothing on disk is trusted.
    """
    fingerprint = _source_fingerprint()
    with _MEMO_LOCK:
        if _MEMO["fingerprint"] == fingerprint:
            return dict(_MEMO["servers"])
    servers = compute_projection()
    with _MEMO_LOCK:
        changed = _MEMO["servers"] != servers
        _MEMO["fingerprint"] = fingerprint
        _MEMO["servers"] = servers
    if servers and changed:
        logger.info(
            "Shared agent home is not writable from this data home; delivering %d "
            "mcp.json server(s) at session level: %s",
            len(servers),
            ", ".join(sorted(servers)),
        )
    return dict(servers)


def clear_projection() -> None:
    """Forget the projection once this instance owns its spec again."""
    with _MEMO_LOCK:
        _MEMO["fingerprint"] = None
        _MEMO["servers"] = {}


def _shared_spec_names() -> set[str] | None:
    """Aliases the shared primary spec declares, or ``None`` when unreadable."""
    from kiro_crew.agent import kiro_agents_dir_path
    from kiro_crew.agent_discovery import _read_agent_spec

    path = kiro_agents_dir_path() / AGENT_FILENAME
    if not path.is_file():
        return set()
    spec = _read_agent_spec(path, operation="declined_home_session_mcp", source="unknown")
    if spec is None:
        return None
    servers = spec.get("mcpServers")
    if not isinstance(servers, dict):
        return set()
    return {mcp_server_alias(str(n)) for n in servers}


def _env_pairs(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, dict):
        return []
    return [{"name": str(k), "value": str(v)} for k, v in raw.items()]


def session_servers(
    agent: str | None,
    *,
    work_dir: str | Path | None = None,
    present: Collection[str] = (),
) -> list[dict[str, Any]]:
    """ACP ``session/new`` elements for this session's undelivered servers.

    ``present`` is every name the caller's array already carries (the broker
    stubs); a server under that name, raw or aliased, is never added again.
    Empty -- the pre-existing behaviour -- unless ALL of these hold: the session
    runs the user-level primary agent, the shared spec write is refused right
    now, no registry ceiling applies, and the shared spec is readable. Blocking
    file I/O; callers are already off the event loop. Never raises.
    """
    try:
        return _session_servers(agent, work_dir=work_dir, present=present)
    except Exception:
        logger.warning("session-level mcp.json delivery failed; delivering none", exc_info=True)
        return []


def _session_servers(
    agent: str | None,
    *,
    work_dir: str | Path | None,
    present: Collection[str],
) -> list[dict[str, Any]]:
    from kiro_crew.agent import (  # circular at module scope
        _decline_shared_agent_home,
        _mcp_registry_mode,
        _project_shadow_of,
    )

    if (agent or PRIMARY_AGENT) != PRIMARY_AGENT:
        return []
    if work_dir and (
        _project_shadow_of(PRIMARY_AGENT, work_dir, markdown_specs=False, dispatchable_only=True)
        is not None
    ):
        # The checkout declares its own agent of this name: the session is not
        # running the user-level spec this projection supplements.
        return []
    if _decline_shared_agent_home(audit=False) is None:
        # The instance writes its own spec, which already mounts every server.
        return []
    if _mcp_registry_mode():
        return []
    projection = refresh_projection()
    if not projection:
        return []
    declared = _shared_spec_names()
    if declared is None:
        # Cannot tell what the spec already mounts; delivering could register a
        # server twice, so deliver nothing.
        return []
    taken = declared | {mcp_server_alias(str(n)) for n in present} | set(present)
    out: list[dict[str, Any]] = []
    for alias, entry in sorted(projection.items()):
        if alias in taken:
            continue
        out.append(
            {
                "name": alias,
                "command": entry["command"],
                "args": list(entry["args"]),
                "env": _env_pairs(entry["env"]),
            }
        )
    return out
