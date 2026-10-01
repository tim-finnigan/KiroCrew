"""Tests for ``GET /api/project/tree``."""

from __future__ import annotations

import errno
import json
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from kiro_crew.dashboard.handlers import api_project_tree
from kiro_crew.security.redaction import _PATH_SEGMENT_DISCRIMINATOR_SEP, _path_segment_label


def _deny_directory_read(monkeypatch, *denied: os.PathLike[str] | str) -> None:
    """Make ``scandir`` refuse the named directories, the way a mode-000 directory
    does for a non-root process.

    A SEAM, not a real permission bit: ``chmod 000`` denies nothing to root, to
    Windows (``chmod`` there touches only the read-only attribute, which does not
    govern listing) or on a filesystem that ignores POSIX modes, so a test built
    on it would have to skip on those hosts -- and the assertions it guards would
    never execute on those CI shards. The walk reads ``scandir`` off the ``os``
    module for every folder it opens and records a folder whose read raises as
    unreadable, so refusing it here exercises the handler's own recording path
    on every platform, including the process's own kernel saying yes.
    """
    real_scandir = os.scandir
    refused = {os.path.realpath(os.fspath(d)) for d in denied}

    def scandir(path=".", *args, **kwargs):
        # ``rmtree`` and friends pass a file descriptor: not a path, never refused.
        if isinstance(path, (str, os.PathLike)) and os.path.realpath(os.fspath(path)) in refused:
            raise PermissionError(errno.EACCES, "Permission denied", os.fspath(path))
        return real_scandir(path, *args, **kwargs)

    monkeypatch.setattr(os, "scandir", scandir)


def _pretend_directory_symlink(monkeypatch, link: os.PathLike[str] | str) -> None:
    """Make ``os.path.islink`` answer yes for one real directory.

    Creating a symlink needs a privilege on Windows, so a real one would skip the
    test there. The walk asks ``platform_compat.is_link_or_junction`` before it
    queues a folder, which asks ``os.path.islink`` first, so one patched answer
    gives the symlink-to-a-directory shape: listed among the subdirectories,
    never walked into.
    """
    real_islink = os.path.islink
    target = os.path.realpath(os.fspath(link))

    def islink(path) -> bool:
        return os.path.realpath(os.fspath(path)) == target or real_islink(path)

    monkeypatch.setattr(os.path, "islink", islink)


def _count_project_reads(monkeypatch, project: os.PathLike[str] | str) -> dict[str, int]:
    """Count each ``scandir`` of a folder inside *project*, and nothing else.

    A path filter, not a wrapper around every call: the event loop, the test
    client and anything else in the process may list a directory while the
    patch is live, and they must neither be counted nor get anything but the
    real iterator back.
    """
    real_scandir = os.scandir
    root = os.path.realpath(os.fspath(project))
    counts: dict[str, int] = {}

    def scandir(path=".", *args, **kwargs):
        if isinstance(path, (str, os.PathLike)):
            resolved = os.path.realpath(os.fspath(path))
            if resolved == root or resolved.startswith(root + os.sep):
                counts[resolved] = counts.get(resolved, 0) + 1
        return real_scandir(path, *args, **kwargs)

    monkeypatch.setattr(os, "scandir", scandir)
    return counts


class _Slot:
    def __init__(self, project: str) -> None:
        self.project = project


class _State:
    def __init__(self, *projects: str) -> None:
        self._slots = {f"s{i}": _Slot(p) for i, p in enumerate(projects)}


def _make_app(*known: str) -> web.Application:
    app = web.Application()
    app["state"] = _State(*known)
    app.router.add_get("/api/project/tree", api_project_tree)
    return app


@pytest.fixture(autouse=True)
def passthrough_sandbox(monkeypatch):
    """Run git unwrapped: CI runners have no sandbox backend, and the handlers
    fail CLOSED without one. The chokepoint's own behavior is covered by
    test_sandbox*/test_spawn_audit; these tests exercise the listing logic.
    """
    from kiro_crew.dashboard.handlers import files as files_mod

    monkeypatch.setattr(
        files_mod,
        "sandboxed_spawn_argv",
        lambda argv, mode="standard", **kw: (list(argv), dict(os.environ), None),
    )


@pytest.fixture()
def mock_sel():
    with patch("kiro_crew.dashboard.handlers.sel") as m:
        m.return_value = MagicMock()
        yield m.return_value


@pytest.fixture()
def plain_project(tmp_path, monkeypatch):
    """A project directory that is NOT inside any repository, wherever ``tmp_path`` is.

    The walk branch is what these tests exercise, and "not a repository" is not a
    property ``tmp_path`` has on every host: a harness that pins ``TMPDIR`` under
    the checkout gives it a real ``.git`` among its ancestors, git's upward
    discovery finds it, and the handler answers from ``ls-files`` -- ``repo: true``
    and git's ordering -- instead of walking. ``GIT_CEILING_DIRECTORIES`` is git's
    own seam for that walk (discovery stops below the named directory) and the
    handler builds its git environment from ``os.environ``, so the state is
    constructed here rather than assumed of the host. The project is a CHILD of
    the ceiling because git checks its starting directory before consulting it.
    The directory is not created: each test lays out its own tree under it.
    """
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    return tmp_path / "plain"


def _git(cwd, *args) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "T",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "T",
            "GIT_COMMITTER_EMAIL": "t@example.com",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
        },
    )


@pytest.fixture(scope="session")
def _repo_template(tmp_path_factory):
    root = tmp_path_factory.mktemp("tree-seed") / "proj"
    root.mkdir()
    _git(root, "init", "-q", "-b", "trunk")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "T")
    (root / "a.txt").write_text("line1\n")
    (root / "src").mkdir()
    (root / "src" / "mod.py").write_text("x = 1\n")
    (root / ".gitignore").write_text("ignored.log\n")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "initial commit")
    return root


@pytest.fixture()
def repo(tmp_path, _repo_template):
    root = tmp_path / "proj"
    shutil.copytree(_repo_template, root)
    return root


class TestProjectTree:
    @pytest.mark.asyncio
    async def test_missing_path_is_400(self, mock_sel):
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.get("/api/project/tree")
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_unknown_project_is_403(self, tmp_path, mock_sel):
        known = tmp_path / "known"
        known.mkdir()
        other = tmp_path / "other"
        other.mkdir()
        async with TestClient(TestServer(_make_app(str(known)))) as client:
            resp = await client.get(f"/api/project/tree?path={other}")
        assert resp.status == 403

    @pytest.mark.asyncio
    async def test_vanished_directory_response_is_redacted(self, tmp_path, mock_sel, monkeypatch):
        """A known project dir deleted between the allow-list match and the stat.

        The early return still echoes the path, so it goes through the same egress
        redaction as the listing below it -- a project directory can carry a
        credential-shaped segment, and this arm is reachable, not defensive.
        """
        from kiro_crew.dashboard.handlers import files as files_mod

        known = tmp_path / "AKIAIOSFODNN7EXAMPLE"
        known.mkdir()
        monkeypatch.setattr(files_mod.os.path, "isdir", lambda p: False)
        async with TestClient(TestServer(_make_app(str(known)))) as client:
            resp = await client.get(f"/api/project/tree?path={known}")
            data = await resp.json()
        assert data["paths"] == []
        assert "AKIAIOSFODNN7EXAMPLE" not in data["root"]

    @pytest.mark.asyncio
    async def test_not_a_directory_root_takes_the_path_aware_redactor(
        self, tmp_path, mock_sel, monkeypatch
    ):
        """The early arm carries the same absolute project path as the listing.

        Same defect and same fix as /api/project/git's repoRoot: a macOS
        per-user temp root scans as one high-entropy token and the Files tab
        renders `[REDACTED: credential]` where the path belongs. Reached with a
        real non-directory rather than by patching `os.path.isdir`, which is
        process-global and breaks unrelated lazy imports.
        """
        from kiro_crew.dashboard.handlers import files as files_mod

        known = tmp_path / "project.txt"
        known.write_text("not a directory")

        seen: list[str] = []
        real = files_mod._redact_project_path

        def spy(value: str) -> str:
            seen.append(value)
            return real(value)

        monkeypatch.setattr(files_mod, "_redact_project_path", spy)
        async with TestClient(TestServer(_make_app(str(known)))) as client:
            resp = await client.get(f"/api/project/tree?path={known}")
            data = await resp.json()
        assert data["paths"] == []
        assert seen == [str(known)]

    @pytest.mark.asyncio
    async def test_listing_root_takes_the_path_aware_redactor(self, repo, mock_sel, monkeypatch):
        """`root` is absolute and takes the path-aware redactor; `paths` are
        project-relative, carry no OS temp prefix, and stay on the canonical
        one. Pinned on the call because the two agree off-Darwin."""
        from kiro_crew.dashboard.handlers import files as files_mod

        seen: list[str] = []
        real = files_mod._redact_project_path

        def spy(value: str) -> str:
            seen.append(value)
            return real(value)

        monkeypatch.setattr(files_mod, "_redact_project_path", spy)
        async with TestClient(TestServer(_make_app(str(repo)))) as client:
            resp = await client.get(f"/api/project/tree?path={repo}")
            data = await resp.json()
        assert data["repo"] is True
        assert seen == [str(repo)]
        assert "a.txt" in data["paths"]

    @pytest.mark.asyncio
    async def test_git_repo_lists_tracked_and_untracked(self, repo, mock_sel):
        (repo / "untracked.md").write_text("hi\n")
        (repo / "ignored.log").write_text("nope\n")
        async with TestClient(TestServer(_make_app(str(repo)))) as client:
            resp = await client.get(f"/api/project/tree?path={repo}")
            data = await resp.json()
        assert data["repo"] is True
        assert "a.txt" in data["paths"]
        assert "src/mod.py" in data["paths"]
        assert "untracked.md" in data["paths"]
        assert "ignored.log" not in data["paths"]

    @pytest.mark.asyncio
    async def test_listing_disables_the_repo_writable_fsmonitor_hook(
        self, repo, mock_sel, monkeypatch
    ):
        # `core.fsmonitor` names a command git SPAWNS and lives in the
        # repository's own config, which an agent can write — so a tree listing
        # must not let it run. Pinned on the argv because the flag is invisible
        # in the response: a listing with the hook enabled looks identical.
        seen: list[list[str]] = []
        from kiro_crew.dashboard.handlers import files as files_mod

        real = files_mod._run_git_bounded

        def spy(argv, **kwargs):
            seen.append(list(argv))
            return real(argv, **kwargs)

        monkeypatch.setattr(files_mod, "_run_git_bounded", spy)
        async with TestClient(TestServer(_make_app(str(repo)))) as client:
            resp = await client.get(f"/api/project/tree?path={repo}")
            assert resp.status == 200
        ls_argv = next(a for a in seen if "ls-files" in a)
        assert "core.fsmonitor=" in ls_argv
        assert ls_argv.index("-c") < ls_argv.index("ls-files")

    @pytest.mark.asyncio
    async def test_non_repo_walk_skips_heavy_and_hidden_dirs(self, plain_project, mock_sel):
        plain = plain_project
        (plain / "node_modules" / "dep").mkdir(parents=True)
        (plain / "node_modules" / "dep" / "index.js").write_text("x")
        (plain / ".hidden").mkdir()
        (plain / ".hidden" / "secret.txt").write_text("x")
        (plain / "docs").mkdir()
        (plain / "docs" / "readme.md").write_text("x")
        (plain / "top.txt").write_text("x")
        async with TestClient(TestServer(_make_app(str(plain)))) as client:
            resp = await client.get(f"/api/project/tree?path={plain}")
            data = await resp.json()
        assert data["repo"] is False
        assert sorted(data["paths"]) == ["docs/readme.md", "top.txt"]

    @pytest.mark.asyncio
    async def test_redaction_collision_paths_stay_distinct(self, repo, mock_sel):
        """Two genuinely-different paths that redact() collapses to one string
        must BOTH appear in the listing, as two distinct redacted entries.

        Uses a real ls-files collision: two files whose only differing segment is
        a credential-shaped token (distinct AKIA... ids, each 4-letter prefix +
        16 uppercase alphanumerics) both flatten to
        ``[REDACTED: credential]_model.txt`` under the whole-string redact().
        Each path is redacted with ``redact_path_segments`` so each member of
        the collision carries an opaque label keyed per gateway process --
        distinct between the two and stable across responses -- and neither
        vanishes; the de-dup behind it still guards a true collision, and the
        raw tokens never leak.
        """
        # Two DISTINCT keys are the point: the test proves two different
        # credential-shaped names collapse to ONE placeholder. key_a is the
        # documented example id Semgrep allowlists; key_b must stay a split
        # literal because detected-aws-access-key-id-value matches an
        # AKIA-shaped literal and cannot tell a fixture from a real leak. Do
        # not re-join it -- the runtime value is identical and CI, not the
        # test, is what breaks.
        key_a = "AKIAIOSFODNN7EXAMPLE"
        key_b = "AKIA" + "JKLMNOPQRSTUVWXY"
        (repo / f"{key_a}_model.txt").write_text("one\n")
        (repo / f"{key_b}_model.txt").write_text("two\n")
        async with TestClient(TestServer(_make_app(str(repo)))) as client:
            resp = await client.get(f"/api/project/tree?path={repo}")
            data = await resp.json()
        paths = data["paths"]
        # Both files survive, each redacted and distinct from the other, each
        # carrying exactly the keyed label of its own original segment...
        sep = _PATH_SEGMENT_DISCRIMINATOR_SEP
        redacted = [p for p in paths if p.startswith(f"[REDACTED: credential]_model.txt{sep}")]
        assert sorted(redacted) == sorted(
            f"[REDACTED: credential]_model.txt{sep}{_path_segment_label(f'{k}_model.txt')}"
            for k in (key_a, key_b)
        ), paths
        assert len(paths) == len(set(paths))
        # ...and the raw credential-shaped tokens never leak.
        assert key_a not in "\n".join(paths)
        assert key_b not in "\n".join(paths)
        # Non-colliding entries survive unchanged.
        assert "a.txt" in paths
        assert "src/mod.py" in paths

    @pytest.mark.asyncio
    async def test_a_redacted_path_labels_identically_across_responses(self, repo, mock_sel):
        """The dashboard joins the tree response with the git-status response
        by path, so a redacted path must carry the same label in every response
        this process serves -- here, two tree responses, one of which lists a
        colliding neighbour and one of which does not."""
        key_a = "AKIAIOSFODNN7EXAMPLE"
        key_b = "AKIA" + "JKLMNOPQRSTUVWXY"
        (repo / f"{key_a}_model.txt").write_text("one\n")
        async with TestClient(TestServer(_make_app(str(repo)))) as client:
            resp = await client.get(f"/api/project/tree?path={repo}")
            alone = await resp.json()
            (repo / f"{key_b}_model.txt").write_text("two\n")
            resp = await client.get(f"/api/project/tree?path={repo}")
            with_neighbour = await resp.json()
        sep = _PATH_SEGMENT_DISCRIMINATOR_SEP
        label_a = (
            f"[REDACTED: credential]_model.txt{sep}{_path_segment_label(f'{key_a}_model.txt')}"
        )
        assert label_a in alone["paths"]
        assert label_a in with_neighbour["paths"]
        assert (
            len([p for p in with_neighbour["paths"] if p.startswith("[REDACTED: credential]")]) == 2
        )

    @pytest.mark.asyncio
    async def test_a_true_redaction_collision_is_still_deduplicated(
        self, repo, mock_sel, monkeypatch
    ):
        """When the path helper (``redact_path_segments``) hands back the same
        string for two paths, the de-dup keeps first occurrence so
        @pierre/trees never sees adjacent identical entries."""
        from kiro_crew.dashboard.handlers import files as files_mod

        monkeypatch.setattr(
            files_mod,
            "redact_path_segments",
            lambda p, r=None: (
                "[REDACTED: credential]_model.txt" if p.endswith("_model.txt") else p
            ),
        )
        (repo / "one_model.txt").write_text("one\n")
        (repo / "two_model.txt").write_text("two\n")
        async with TestClient(TestServer(_make_app(str(repo)))) as client:
            resp = await client.get(f"/api/project/tree?path={repo}")
            data = await resp.json()
        paths = data["paths"]
        assert paths.count("[REDACTED: credential]_model.txt") == 1
        assert len(paths) == len(set(paths))
        assert "a.txt" in paths

    @pytest.mark.asyncio
    async def test_walk_caps_entries_and_flags_truncation(
        self, plain_project, mock_sel, monkeypatch
    ):
        from kiro_crew.dashboard.handlers import files as files_mod

        monkeypatch.setattr(files_mod, "_PROJECT_TREE_MAX_ENTRIES", 2)
        plain = plain_project
        plain.mkdir()
        for name in ("a.txt", "b.txt", "c.txt"):
            (plain / name).write_text("x")
        async with TestClient(TestServer(_make_app(str(plain)))) as client:
            resp = await client.get(f"/api/project/tree?path={plain}")
            data = await resp.json()
        assert data["truncated"] is True
        assert len(data["paths"]) == 2
        assert data["directories"] == []
        assert data["truncatedDirectories"] == [""]

    @pytest.mark.asyncio
    async def test_walk_spends_the_cap_on_folder_rows_first_and_samples_files_fairly(
        self, plain_project, mock_sel, monkeypatch
    ):
        """One cap covers files AND folder rows. Folder rows come first,
        shallowest first, but never past half the cap while there are files to
        show; the files share the rest round-robin by direct parent, so no one
        folder takes it all, and every shown folder that lost something is
        named -- ``late`` lost its ``nested`` row."""
        from kiro_crew.dashboard.handlers import files as files_mod

        monkeypatch.setattr(files_mod, "_PROJECT_TREE_MAX_ENTRIES", 8)
        plain = plain_project
        for directory in ("alpha", "beta", "late/nested"):
            target = plain / directory
            target.mkdir(parents=True)
            for index in range(3):
                (target / f"{index}.txt").write_text("x")
        (plain / "empty").mkdir()

        async with TestClient(TestServer(_make_app(str(plain)))) as client:
            resp = await client.get(f"/api/project/tree?path={plain}")
            data = await resp.json()

        assert data["truncated"] is True
        assert data["directories"] == ["alpha", "beta", "empty", "late"]
        assert data["paths"] == ["alpha/0.txt", "alpha/1.txt", "beta/0.txt", "beta/1.txt"]
        assert data["truncatedDirectories"] == ["alpha", "beta", "late"]

    @pytest.mark.asyncio
    async def test_walk_caps_folder_rows_too_shallowest_first(
        self, plain_project, mock_sel, monkeypatch
    ):
        """With no file to show, folder rows take the whole cap, breadth-first:
        the top-level folders keep their rows ahead of anything nested, and
        every shown folder that lost a child row is named as truncated -- the
        root included, which lost ``d3`` and ``d4``."""
        from kiro_crew.dashboard.handlers import files as files_mod

        monkeypatch.setattr(files_mod, "_PROJECT_TREE_MAX_ENTRIES", 3)
        plain = plain_project
        for index in range(5):
            (plain / f"d{index}" / "sub").mkdir(parents=True)

        async with TestClient(TestServer(_make_app(str(plain)))) as client:
            resp = await client.get(f"/api/project/tree?path={plain}")
            data = await resp.json()

        assert data["directories"] == ["d0", "d1", "d2"]
        assert data["paths"] == []
        assert data["truncated"] is True
        assert data["truncatedDirectories"] == ["", "d0", "d1", "d2"]

    @pytest.mark.asyncio
    async def test_walk_keeps_root_files_when_folders_alone_would_fill_the_cap(
        self, plain_project, mock_sel, monkeypatch
    ):
        """More folders than the cap must not hide every file: the root's
        ``README.md`` is the first file a workspace is opened for, and in tree
        mode the rail's name filter searches only the listed rows."""
        from kiro_crew.dashboard.handlers import files as files_mod

        monkeypatch.setattr(files_mod, "_PROJECT_TREE_MAX_ENTRIES", 4)
        plain = plain_project
        for index in range(6):
            (plain / f"pkg{index}").mkdir(parents=True)
        (plain / "README.md").write_text("x")

        async with TestClient(TestServer(_make_app(str(plain)))) as client:
            resp = await client.get(f"/api/project/tree?path={plain}")
            data = await resp.json()

        assert data["paths"] == ["README.md"]
        assert data["directories"] == ["pkg0", "pkg1", "pkg2"]
        assert data["truncatedDirectories"] == [""]

    @pytest.mark.asyncio
    async def test_spare_rows_go_to_folders_without_evicting_files_that_fit(
        self, plain_project, mock_sel, monkeypatch
    ):
        """Files counted in folders past the row budget leave file rows unused,
        and those go back to folder rows -- but the folders gaining a row do not
        then share the file rows, which would evict the root's README.md."""
        from kiro_crew.dashboard.handlers import files as files_mod

        monkeypatch.setattr(files_mod, "_PROJECT_TREE_MAX_ENTRIES", 6)
        plain = plain_project
        for index in range(6):
            (plain / f"pkg{index}").mkdir(parents=True)
        (plain / "LICENSE").write_text("x")
        (plain / "README.md").write_text("x")
        for index in range(5):
            (plain / "pkg3" / f"m{index}.py").write_text("x")

        async with TestClient(TestServer(_make_app(str(plain)))) as client:
            resp = await client.get(f"/api/project/tree?path={plain}")
            data = await resp.json()

        assert data["paths"] == ["LICENSE", "README.md"]
        assert data["directories"] == ["pkg0", "pkg1", "pkg2", "pkg3"]
        assert data["truncatedDirectories"] == ["", "pkg3"]

    @pytest.mark.asyncio
    async def test_walk_of_a_big_tree_returns_at_most_the_cap_in_rows(
        self, plain_project, mock_sel, monkeypatch
    ):
        """A large non-git project is listed every 10 s while its tree is open,
        so the rows per listing are what the gateway pays for each poll: files
        and folder rows together stop at the cap, and the cut is flagged."""
        from kiro_crew.dashboard.handlers import files as files_mod

        monkeypatch.setattr(files_mod, "_PROJECT_TREE_MAX_ENTRIES", 50)
        plain = plain_project
        for top in range(20):
            nested = plain / f"top{top:02d}" / "sub"
            nested.mkdir(parents=True)
            for index in range(10):
                (plain / f"top{top:02d}" / f"{index}.txt").write_text("x")
                (nested / f"{index}.txt").write_text("x")

        async with TestClient(TestServer(_make_app(str(plain)))) as client:
            resp = await client.get(f"/api/project/tree?path={plain}")
            data = await resp.json()

        assert len(data["paths"]) + len(data["directories"]) == 50
        assert data["truncated"] is True
        # Every top-level folder keeps its row, and files still get theirs.
        assert {f"top{top:02d}" for top in range(20)} <= set(data["directories"])
        assert len(data["paths"]) == 25
        assert set(data["truncatedDirectories"]) <= {"", *data["directories"]}

    @pytest.mark.parametrize("big", ["data", "zz_data"])
    @pytest.mark.asyncio
    async def test_one_large_folder_does_not_starve_its_siblings_of_the_scan(
        self, big, plain_project, mock_sel, monkeypatch
    ):
        """One folder holding more entries than the whole scan budget must not
        starve its siblings, wherever it sorts: before ``src/`` it would spend
        the budget before ``src/`` is read, after it it would spend what the
        next depth needs to read ``src/lib/``. Each folder of a depth reads at
        most an even split of what is left, with the depths below counted as
        one more claimant, so ``src/`` and ``src/lib/`` are read in full and
        only the large folder is cut."""
        from kiro_crew.dashboard.handlers import files as files_mod

        monkeypatch.setattr(files_mod, "_PROJECT_TREE_SCAN_LIMIT", 100)
        plain = plain_project
        (plain / big).mkdir(parents=True)
        for index in range(110):
            (plain / big / f"{index:04d}.csv").write_text("x")
        (plain / "src" / "lib").mkdir(parents=True)
        (plain / "src" / "main.py").write_text("x")
        (plain / "src" / "lib" / "util.py").write_text("x")

        async with TestClient(TestServer(_make_app(str(plain)))) as client:
            resp = await client.get(f"/api/project/tree?path={plain}")
            data = await resp.json()

        assert "src/lib" in data["directories"]
        assert "src/main.py" in data["paths"]
        assert "src/lib/util.py" in data["paths"]
        assert big in data["truncatedDirectories"]
        assert not {"src", "src/lib"} & set(data["truncatedDirectories"])

    @pytest.mark.asyncio
    async def test_a_folder_the_scan_budget_never_reaches_is_named_and_never_read(
        self, plain_project, mock_sel, monkeypatch
    ):
        """A budget of one entry reads the root's only entry, ``later/``, and
        nothing else: the folder that read named is shown and flagged, never
        called empty, and the walk does not open it."""
        from kiro_crew.dashboard.handlers import files as files_mod

        plain = plain_project
        (plain / "later").mkdir(parents=True)
        (plain / "later" / "inside.txt").write_text("x")
        monkeypatch.setattr(files_mod, "_PROJECT_TREE_SCAN_LIMIT", 1)
        opened = _count_project_reads(monkeypatch, plain)

        async with TestClient(TestServer(_make_app(str(plain)))) as client:
            resp = await client.get(f"/api/project/tree?path={plain}")
            data = await resp.json()

        assert opened == {os.path.realpath(plain): 1}
        assert data["directories"] == ["later"]
        assert data["paths"] == []
        assert data["truncated"] is True
        assert data["truncatedDirectories"] == ["later"]

    @pytest.mark.asyncio
    async def test_walk_reads_each_folder_once(self, plain_project, mock_sel, monkeypatch):
        """One pass: each folder is read once, never once to count and again to
        list. A nested tree, so a second read of any level shows."""
        plain = plain_project
        (plain / "a" / "b" / "c").mkdir(parents=True)
        (plain / "a" / "b" / "c" / "leaf.txt").write_text("x")
        (plain / "a" / "one.txt").write_text("x")
        (plain / "z").mkdir()
        opened = _count_project_reads(monkeypatch, plain)
        async with TestClient(TestServer(_make_app(str(plain)))) as client:
            resp = await client.get(f"/api/project/tree?path={plain}")
            data = await resp.json()

        assert data["paths"] == ["a/one.txt", "a/b/c/leaf.txt"]
        expected = {os.path.realpath(plain / p) for p in (".", "a", "a/b", "a/b/c", "z")}
        assert opened == {path: 1 for path in expected}

    @pytest.mark.asyncio
    async def test_a_listing_that_fails_part_way_is_an_unreadable_row(
        self, plain_project, mock_sel, monkeypatch
    ):
        """A read can fail after ``scandir`` opened the folder -- the iterator
        raises on a later entry. What it yielded first is not a listing of the
        folder, so none of it is shown: the folder is a row named unreadable,
        exactly as when ``scandir`` itself refuses."""
        plain = plain_project
        (plain / "flaky").mkdir(parents=True)
        for name in ("a.txt", "b.txt", "c.txt"):
            (plain / "flaky" / name).write_text("x")
        flaky = os.path.realpath(plain / "flaky")
        real_scandir = os.scandir

        class _FailsAfterOne:
            def __init__(self, inner):
                self._inner = inner
                self._yielded = 0

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self._inner.close()

            def __iter__(self):
                return self

            def __next__(self):
                if self._yielded:
                    raise PermissionError(errno.EACCES, "Permission denied", flaky)
                self._yielded += 1
                return next(self._inner)

            def close(self):
                self._inner.close()

        def scandir(path=".", *args, **kwargs):
            inner = real_scandir(path, *args, **kwargs)
            if isinstance(path, (str, os.PathLike)) and os.path.realpath(path) == flaky:
                return _FailsAfterOne(inner)
            return inner

        monkeypatch.setattr(os, "scandir", scandir)
        async with TestClient(TestServer(_make_app(str(plain)))) as client:
            resp = await client.get(f"/api/project/tree?path={plain}")
            data = await resp.json()

        assert data["directories"] == ["flaky"]
        assert data["unreadableDirectories"] == ["flaky"]
        assert data["paths"] == []

    @pytest.mark.asyncio
    async def test_a_root_cut_by_the_scan_limit_is_truncated_not_hidden_only(
        self, plain_project, mock_sel, monkeypatch
    ):
        """A root whose first entries are all hidden folders, cut by the scan
        limit, has not been read in full: what follows is unknown, so it is
        truncated -- never judged hidden-only or empty on what it did read."""
        from kiro_crew.dashboard.handlers import files as files_mod

        monkeypatch.setattr(files_mod, "_PROJECT_TREE_SCAN_LIMIT", 3)
        plain = plain_project
        for index in range(5):
            (plain / f".cache{index}").mkdir(parents=True)

        async with TestClient(TestServer(_make_app(str(plain)))) as client:
            resp = await client.get(f"/api/project/tree?path={plain}")
            data = await resp.json()

        assert data["paths"] == [] and data["directories"] == []
        assert data["truncated"] is True
        assert data["truncatedDirectories"] == [""]
        assert data["hiddenOnlyDirectories"] == []

    def test_git_folders_are_ordered_the_way_the_walk_discovers_them(self):
        """Breadth-first, then segment by segment: ``a/z`` before ``a-b/c``,
        because the walk reads ``a`` before ``a-b`` -- a whole-string sort would
        put ``a-b/c`` first (``-`` sorts before ``/``) and the row cap would
        keep a different folder on each branch."""
        from kiro_crew.dashboard.handlers.files import _project_tree_git_layout

        rows, files = _project_tree_git_layout(sorted(["a-b/c/x.txt", "a/z/y.txt", "top.txt"]))

        assert rows == ["a", "a-b", "a/z", "a-b/c"]
        assert files == {"a-b/c": ["x.txt"], "a/z": ["y.txt"], "": ["top.txt"]}

    @pytest.mark.asyncio
    async def test_a_failed_git_listing_falls_back_to_the_bounded_walk(
        self, repo, mock_sel, monkeypatch
    ):
        """A repository whose ``ls-files`` overflows the output cap or the
        timeout (or a host with no sandbox backend) gets a nonzero return code
        and falls into the walk, so the walk's bound is what protects it."""
        from kiro_crew.dashboard.handlers import files as files_mod

        real = files_mod._run_git_bounded

        def overflowing(argv, **kwargs):
            if "ls-files" in argv:
                return -9, "", True
            return real(argv, **kwargs)

        monkeypatch.setattr(files_mod, "_run_git_bounded", overflowing)
        monkeypatch.setattr(files_mod, "_PROJECT_TREE_MAX_ENTRIES", 2)
        async with TestClient(TestServer(_make_app(str(repo)))) as client:
            resp = await client.get(f"/api/project/tree?path={repo}")
            data = await resp.json()

        assert data["repo"] is False
        assert data["directories"] == ["src"]
        assert len(data["paths"]) == 1
        assert data["truncated"] is True
        assert ".git" not in json.dumps(data["directories"])

    @pytest.mark.parametrize("layout", ["walk", "git"])
    @pytest.mark.asyncio
    async def test_redaction_and_serialization_run_off_the_event_loop(
        self, layout, plain_project, repo, mock_sel, monkeypatch
    ):
        """Redacting every row and serializing the body are linear in the size
        of the listing, and the tree refetches every 10 s: on the event loop
        they stall every other request the gateway serves."""
        from kiro_crew.dashboard.handlers import files as files_mod

        if layout == "walk":
            project = plain_project
            (project / "docs").mkdir(parents=True)
            (project / "docs" / "readme.md").write_text("x")
        else:
            project = repo
        loop_thread = threading.get_ident()
        threads: dict[str, set[int]] = {"segments": set(), "body": set()}
        real_segments = files_mod.redact_path_segments
        real_body = files_mod._project_tree_body

        def segments(path, redactor=None):
            threads["segments"].add(threading.get_ident())
            return real_segments(path, redactor)

        def body(result):
            threads["body"].add(threading.get_ident())
            return real_body(result)

        monkeypatch.setattr(files_mod, "redact_path_segments", segments)
        monkeypatch.setattr(files_mod, "_project_tree_body", body)
        async with TestClient(TestServer(_make_app(str(project)))) as client:
            resp = await client.get(f"/api/project/tree?path={project}")
            assert resp.status == 200
            assert resp.content_type == "application/json"
            data = await resp.json()

        assert data["repo"] is (layout == "git")
        assert threads["segments"] and threads["body"]
        assert loop_thread not in threads["segments"] | threads["body"]

    @pytest.mark.asyncio
    async def test_walk_under_cap_keeps_all_files_and_directory_rows(
        self, plain_project, mock_sel, monkeypatch
    ):
        from kiro_crew.dashboard.handlers import files as files_mod

        monkeypatch.setattr(files_mod, "_PROJECT_TREE_MAX_ENTRIES", 4)
        plain = plain_project
        (plain / "docs").mkdir(parents=True)
        (plain / "docs" / "readme.md").write_text("docs")
        (plain / "empty").mkdir()
        (plain / "top.txt").write_text("top")

        async with TestClient(TestServer(_make_app(str(plain)))) as client:
            resp = await client.get(f"/api/project/tree?path={plain}")
            data = await resp.json()

        assert data["paths"] == ["top.txt", "docs/readme.md"]
        assert data["directories"] == ["docs", "empty"]
        assert data["truncated"] is False
        assert data["truncatedDirectories"] == []

    @pytest.mark.asyncio
    async def test_walk_reports_directories_left_childless_by_its_own_filter(
        self, plain_project, mock_sel
    ):
        """A folder holding ONLY entries the walk drops (dot-directories, tooling
        caches) comes back as a directory row with nothing beneath it, exactly
        like a folder that is empty on disk. The tree draws a state row under a
        childless folder, and that row may only call the folder empty when it
        is: ``hiddenOnlyDirectories`` names the ones that are not.
        """
        plain = plain_project
        (plain / "_bg" / ".kiro").mkdir(parents=True)
        (plain / "_bg" / ".kiro" / "agent.json").write_text("{}")
        (plain / "caches" / "node_modules").mkdir(parents=True)
        (plain / "empty").mkdir()
        # A hidden entry beside a listed file or a kept subfolder is not the
        # reported case: that folder has rows beneath it.
        (plain / "mixed" / ".hidden").mkdir(parents=True)
        (plain / "mixed" / "kept.txt").write_text("x")
        (plain / "nested" / ".hidden").mkdir(parents=True)
        (plain / "nested" / "sub").mkdir()

        async with TestClient(TestServer(_make_app(str(plain)))) as client:
            resp = await client.get(f"/api/project/tree?path={plain}")
            data = await resp.json()

        assert data["hiddenOnlyDirectories"] == ["_bg", "caches"]
        # Every one of them is still a directory row -- the folder is shown,
        # only its emptiness is qualified.
        assert set(data["hiddenOnlyDirectories"]) <= set(data["directories"])
        assert "empty" in data["directories"]
        assert data["paths"] == ["mixed/kept.txt"]

    @pytest.mark.asyncio
    async def test_a_folder_holding_only_a_directory_symlink_is_not_hidden_only_and_the_link_is_a_row(
        self, plain_project, mock_sel, monkeypatch
    ):
        """``ls deploy`` shows ``current``: a symlink to a directory is a visible,
        navigable entry, so its folder is NOT hidden-only (hidden-only means every
        entry is one the listing filters out by nature -- dot-directories, the
        skip set). The walk never follows a link (against link cycles), so
        nothing beneath it would be listed and the folder would read as empty;
        so the link is listed as a directory
        row of its own and named in ``linkedDirectories``, and nothing beneath it
        is listed. The link is a seam (``_pretend_directory_symlink``) over a real
        directory holding a file, so the assertion runs where symlinks need a
        privilege, and the file beneath proves the walk did not go in.
        """
        plain = plain_project
        (plain / "releases").mkdir(parents=True)
        (plain / "releases" / "kept.txt").write_text("x")
        (plain / "linked" / "current").mkdir(parents=True)
        (plain / "linked" / "current" / "behind-the-link.txt").write_text("x")
        _pretend_directory_symlink(monkeypatch, plain / "linked" / "current")

        async with TestClient(TestServer(_make_app(str(plain)))) as client:
            resp = await client.get(f"/api/project/tree?path={plain}")
            data = await resp.json()

        assert data["hiddenOnlyDirectories"] == []
        assert data["linkedDirectories"] == ["linked/current"]
        # The folder AND the link are rows, in walk (breadth-first) order; the
        # link's target is never walked, so no file beneath it is listed.
        assert data["directories"] == ["linked", "releases", "linked/current"]
        assert data["paths"] == ["releases/kept.txt"]
        assert data["unreadableDirectories"] == []

    @pytest.mark.asyncio
    async def test_a_directory_symlink_named_like_a_skip_directory_is_hidden_and_not_a_row(
        self, plain_project, mock_sel, monkeypatch
    ):
        """``shared/node_modules -> ../store/node_modules`` is filtered exactly
        like the real ``node_modules`` beside it: the name filter applies to
        every entry alike, link or not, so what a folder shows is predictable
        from the name alone (Design lane on ``9f52681b54``). A filtered link is
        a hidden entry -- a folder holding nothing else is hidden-only -- and is
        no row, in ``directories`` or ``linkedDirectories``. A dot-named link
        (``dotted/.cache``) follows the same rule. A link the filter KEEPS
        (``deploy/current``) stays a row of its own, as the test above pins.
        """
        plain = plain_project
        (plain / "store" / "node_modules" / "dep").mkdir(parents=True)
        (plain / "store" / "node_modules" / "dep" / "index.js").write_text("x")
        (plain / "shared" / "node_modules" / "dep").mkdir(parents=True)
        (plain / "shared" / "node_modules" / "dep" / "index.js").write_text("x")
        (plain / "dotted" / ".cache").mkdir(parents=True)
        (plain / "dotted" / ".cache" / "entry").write_text("x")
        _pretend_directory_symlink(monkeypatch, plain / "shared" / "node_modules")
        _pretend_directory_symlink(monkeypatch, plain / "dotted" / ".cache")

        async with TestClient(TestServer(_make_app(str(plain)))) as client:
            resp = await client.get(f"/api/project/tree?path={plain}")
            data = await resp.json()

        # Each folder holds one hidden entry -- a real cache, a linked cache, a
        # linked dot-directory -- and nothing else: all three are hidden-only,
        # none of the hidden entries is a row, and no link is reported.
        assert data["hiddenOnlyDirectories"] == ["dotted", "shared", "store"]
        assert data["linkedDirectories"] == []
        assert data["directories"] == ["dotted", "shared", "store"]
        # Nothing behind a link or inside a hidden directory is listed.
        assert data["paths"] == []
        assert data["unreadableDirectories"] == []

    @pytest.mark.asyncio
    async def test_walk_lists_a_kept_directory_it_could_not_read(
        self, plain_project, mock_sel, monkeypatch
    ):
        """A kept, non-symlink child whose ``scandir`` fails (permission denied)
        must not leave its parent childless: with no row beneath it the parent is
        not hidden-only (the child is no symlink), and the tree would call the
        parent an empty folder -- a lie, ``ls`` shows the child. The unreadable directory
        is instead a row of its own, named in ``unreadableDirectories`` so the
        dashboard can report it above the tree, and the parent is not childless
        at all. Its files are never listed: nothing read them. The refusal is a
        seam (``_deny_directory_read``), so this runs as root and on Windows too.
        """
        plain = plain_project
        locked = plain / "vault" / "locked"
        locked.mkdir(parents=True)
        (locked / "inside.txt").write_text("x")
        (plain / "open").mkdir()
        (plain / "open" / "kept.txt").write_text("x")
        _deny_directory_read(monkeypatch, locked)

        async with TestClient(TestServer(_make_app(str(plain)))) as client:
            resp = await client.get(f"/api/project/tree?path={plain}")
            data = await resp.json()

        # The folder is shown, in walk order; without it ``vault`` is a directory
        # row with nothing beneath it and no qualifier -- the "Empty folder" lie.
        assert data["directories"] == ["open", "vault", "vault/locked"]
        assert data["unreadableDirectories"] == ["vault/locked"]
        assert data["hiddenOnlyDirectories"] == []
        assert data["paths"] == ["open/kept.txt"]
        assert data["truncated"] is False

    @pytest.mark.asyncio
    async def test_walk_names_an_unreadable_root_instead_of_an_empty_workspace(
        self, plain_project, mock_sel, monkeypatch
    ):
        """A ``scandir`` failure on the project root itself leaves the walk with
        nothing read, so the payload
        would be ``paths == [] and directories == []`` -- exactly what a workspace
        with no files in it sends, and the dashboard would paint "No files in this
        workspace yet" over a folder nothing ever read: the same "empty" claim the
        listing refuses to make one level down. The root is no directory row of
        its own (rows are relative to it), so it is named as ``.`` in
        ``unreadableDirectories`` and the dashboard shows its not-readable state
        in place of the empty-workspace notice. The root passes the handler's
        ``isdir`` check and the git probe finds no repository here, so the walk is
        what answers -- as it is for a real mode-000 root, where the probe fails
        closed on the unreadable ``cwd``.
        """
        plain = plain_project
        plain.mkdir()
        (plain / "inside.txt").write_text("x")
        (plain / "nested").mkdir()
        _deny_directory_read(monkeypatch, plain)

        async with TestClient(TestServer(_make_app(str(plain)))) as client:
            resp = await client.get(f"/api/project/tree?path={plain}")
            data = await resp.json()

        assert resp.status == 200
        # ``.`` is not a path segment the redactor touches, so it survives egress.
        assert data["unreadableDirectories"] == ["."]
        assert data["directories"] == []
        assert data["paths"] == []
        assert data["hiddenOnlyDirectories"] == []
        assert data["truncated"] is False
        assert data["repo"] is False

    @pytest.mark.asyncio
    async def test_walk_names_a_root_holding_only_skipped_or_hidden_folders(
        self, plain_project, mock_sel
    ):
        """The hidden-only rule must judge the project root by the same test as
        every folder beneath it. A project directory whose top level holds only
        entries the walk drops (here ``.kiro/`` and a ``node_modules/`` cache)
        yields no file and no kept subdirectory, so the payload is
        ``paths == [] and directories == []`` -- the empty-workspace shape --
        and the dashboard would paint "No files in this workspace yet" over a
        folder that is not empty: the claim this listing refuses to make one
        level down, made about the whole tree. The root is no directory row of
        its own, so it is named as ``.`` in ``hiddenOnlyDirectories``, exactly
        as an unreadable root is named in ``unreadableDirectories``.
        """
        plain = plain_project
        (plain / ".kiro").mkdir(parents=True)
        (plain / ".kiro" / "agent.json").write_text("{}")
        (plain / "node_modules" / "dep").mkdir(parents=True)

        async with TestClient(TestServer(_make_app(str(plain)))) as client:
            resp = await client.get(f"/api/project/tree?path={plain}")
            data = await resp.json()

        assert resp.status == 200
        assert data["hiddenOnlyDirectories"] == ["."]
        assert data["directories"] == []
        assert data["paths"] == []
        assert data["unreadableDirectories"] == []
        assert data["truncated"] is False
        assert data["repo"] is False

    @pytest.mark.asyncio
    async def test_a_root_with_nothing_in_it_is_still_an_empty_workspace(
        self, plain_project, mock_sel
    ):
        """The root rule must not over-reach: a project directory with no entry
        at all is genuinely empty, and the empty-workspace notice is the truth.
        """
        plain = plain_project
        plain.mkdir()

        async with TestClient(TestServer(_make_app(str(plain)))) as client:
            resp = await client.get(f"/api/project/tree?path={plain}")
            data = await resp.json()

        assert data["hiddenOnlyDirectories"] == []
        assert data["directories"] == []
        assert data["paths"] == []

    @pytest.mark.asyncio
    async def test_a_hidden_only_marker_is_redacted_like_its_directory_row(
        self, plain_project, mock_sel
    ):
        """Mutation pin for ``"hiddenOnlyDirectories"`` in the egress redaction
        tuple: a directory whose NAME is credential-shaped is listed in
        ``directories`` redacted, and the dashboard finds it in
        ``hiddenOnlyDirectories`` by string equality to pick the row beneath it.
        Drop the entry from the tuple and the marker leaks the raw name -- and no
        longer matches its own redacted row, so the folder would be called empty.
        """
        plain = plain_project
        (plain / "AKIAIOSFODNN7EXAMPLE" / ".kiro").mkdir(parents=True)

        async with TestClient(TestServer(_make_app(str(plain)))) as client:
            resp = await client.get(f"/api/project/tree?path={plain}")
            data = await resp.json()

        assert "AKIAIOSFODNN7EXAMPLE" not in json.dumps(data)
        assert len(data["directories"]) == 1
        assert data["directories"][0].startswith("[REDACTED: credential]")
        # The join the tree performs: the marker IS the redacted row.
        assert data["hiddenOnlyDirectories"] == data["directories"]

    @pytest.mark.asyncio
    async def test_a_linked_marker_is_redacted_like_its_directory_row(
        self, plain_project, mock_sel, monkeypatch
    ):
        """Mutation pin for ``"linkedDirectories"`` in the egress redaction
        tuple, same shape as the other two: the link's row and the marker
        naming it must be the same redacted string, and the raw credential-shaped
        name must appear nowhere in the body.
        """
        plain = plain_project
        link = plain / "AKIAIOSFODNN7EXAMPLE"
        link.mkdir(parents=True)
        _pretend_directory_symlink(monkeypatch, link)

        async with TestClient(TestServer(_make_app(str(plain)))) as client:
            resp = await client.get(f"/api/project/tree?path={plain}")
            data = await resp.json()

        assert "AKIAIOSFODNN7EXAMPLE" not in json.dumps(data)
        assert len(data["directories"]) == 1
        assert data["directories"][0].startswith("[REDACTED: credential]")
        assert data["linkedDirectories"] == data["directories"]
        # The root holds a visible entry, so it is neither empty nor hidden-only.
        assert data["hiddenOnlyDirectories"] == []

    @pytest.mark.asyncio
    async def test_an_unreadable_marker_is_redacted_like_its_directory_row(
        self, plain_project, mock_sel, monkeypatch
    ):
        """Mutation pin for ``"unreadableDirectories"`` in the egress redaction
        tuple, same shape as the hidden-only pin: the directory row and the
        marker naming it must be the same redacted string, and the raw
        credential-shaped name must appear nowhere in the body.
        """
        plain = plain_project
        locked = plain / "AKIAIOSFODNN7EXAMPLE"
        locked.mkdir(parents=True)
        _deny_directory_read(monkeypatch, locked)

        async with TestClient(TestServer(_make_app(str(plain)))) as client:
            resp = await client.get(f"/api/project/tree?path={plain}")
            data = await resp.json()

        assert "AKIAIOSFODNN7EXAMPLE" not in json.dumps(data)
        assert len(data["directories"]) == 1
        assert data["directories"][0].startswith("[REDACTED: credential]")
        assert data["unreadableDirectories"] == data["directories"]

    def test_truncation_copy_names_the_served_row_cap(self):
        """The state row under a truncated folder and the workspace-level notice
        state the row cap as a literal in every catalog (``10,000``; a payload
        field would be a new contract for one number). Pin each string to
        ``_PROJECT_TREE_MAX_ENTRIES`` so a change to the constant reds every
        locale still naming the old cap, instead of the dashboard stating a
        limit the server does not enforce. Digit grouping follows the locale
        (``10,000`` / ``10.000`` / ``10 000``), so the comparison drops the
        separators between digits and looks for the bare number.
        """
        from kiro_crew.dashboard.handlers.files import _PROJECT_TREE_MAX_ENTRIES

        locales = Path(__file__).resolve().parents[1] / "website" / "src" / "i18n" / "locales"
        # ``en.json`` is the extracted catalog and carries none of the manual keys.
        catalogs = sorted(p for p in locales.glob("*.json") if p.name != "en.json")
        assert len(catalogs) >= 13, [p.name for p in catalogs]
        cap = str(_PROJECT_TREE_MAX_ENTRIES)
        ungroup = re.compile(r"(?<=\d)[,.\s\u202f\u00a0](?=\d)")
        # The cap as a whole number, not a substring: `1000` is inside `10000`,
        # so lowering the constant must still red every catalog naming the old cap.
        names_cap = re.compile(rf"(?<!\d){re.escape(cap)}(?!\d)")
        for path in catalogs:
            catalog = json.loads(path.read_text(encoding="utf-8"))
            copy = {
                "row_truncated": catalog["components"]["workspaceTree"]["row_truncated"],
                "workspace_truncated": catalog["pages"]["chat"]["activityViewer"][
                    "workspace_truncated"
                ],
            }
            for key, text in copy.items():
                flat = ungroup.sub("", text)
                message = f"{path.name} {key}: {text!r} does not name the cap {cap}"
                assert names_cap.search(flat), message

    @pytest.mark.asyncio
    async def test_git_listing_reports_no_hidden_only_directories(self, repo, mock_sel):
        """Inside a repository a directory row exists only as the parent of a
        listed file, so an ignored-only folder is absent rather than childless
        -- the list is empty by construction, and present so the payload shape
        does not depend on which branch answered. ``unreadableDirectories`` is
        empty for the same reason: ``--others`` cannot scan a directory git
        cannot read, so it contributes no untracked file and, with no indexed
        file beneath it, is absent rather than childless; an indexed path
        beneath it still comes from the index and makes it a populated row."""
        (repo / "logs").mkdir()
        (repo / "logs" / "ignored.log").write_text("nope\n")
        async with TestClient(TestServer(_make_app(str(repo)))) as client:
            resp = await client.get(f"/api/project/tree?path={repo}")
            data = await resp.json()
        assert data["repo"] is True
        assert data["hiddenOnlyDirectories"] == []
        assert data["unreadableDirectories"] == []
        # git lists a symlink as a file (the link is the tracked object), so
        # this branch never has a linked directory row to qualify.
        assert data["linkedDirectories"] == []
        assert "logs" not in data["directories"]

    @pytest.mark.asyncio
    async def test_cap_does_not_drop_the_whole_tracked_block(self, tmp_path, mock_sel, monkeypatch):
        """A fat UNTRACKED subtree must not evict every tracked file.

        ``git ls-files --cached --others`` emits all untracked entries as one
        complete block and only then the tracked ones, so capping with a plain
        prefix cut never reaches the tracked block once untracked alone fill it
        -- the whole source tree loses its rows. The listing is sorted before
        the cut so the budget is spent by path, not by which block git happened
        to emit first.
        """
        from kiro_crew.dashboard.handlers import files as files_mod

        monkeypatch.setattr(files_mod, "_PROJECT_TREE_MAX_ENTRIES", 20)
        repo = tmp_path / "repo"
        repo.mkdir()
        _git(repo, "init", "-q", ".")
        tracked = ("README.md", "docs/real.py", "src/real.py")
        for rel in tracked:
            p = repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "init")
        # Untracked (NOT ignored) and larger than the cap on its own. Named to
        # sort after every tracked path, so a sorted cut reaches them all.
        fat = repo / "zz_vendor" / "deep"
        fat.mkdir(parents=True)
        for i in range(40):
            (fat / f"u{i:04d}.txt").write_text("x")

        async with TestClient(TestServer(_make_app(str(repo)))) as client:
            resp = await client.get(f"/api/project/tree?path={repo}")
            data = await resp.json()

        assert data["repo"] is True
        assert data["truncated"] is True
        # One cap covers files and folder rows; the four folders come first.
        assert data["directories"] == ["docs", "src", "zz_vendor", "zz_vendor/deep"]
        assert len(data["paths"]) == 16
        # The point of the fix: every tracked file keeps a row.
        for rel in tracked:
            assert rel in data["paths"], f"{rel} was evicted by the untracked block"
        # ...and the fix does not merely invert the loss: the untracked subtree
        # still spends the remaining budget, so it keeps a row too.
        assert any(p.startswith("zz_vendor/") for p in data["paths"])

    @pytest.mark.asyncio
    async def test_git_listing_spends_the_same_cap_as_the_walk(self, repo, mock_sel, monkeypatch):
        """The git branch spends the one cap the walk does: folder rows
        shallowest first, but never past half the cap while files remain, the
        root's files first among those -- and a file whose folder lost its row
        is not listed either. Every shown folder that lost something is named."""
        from kiro_crew.dashboard.handlers import files as files_mod

        monkeypatch.setattr(files_mod, "_PROJECT_TREE_MAX_ENTRIES", 3)
        for top in ("p", "q"):
            nested = repo / top / "deep"
            nested.mkdir(parents=True)
            (nested / "f.txt").write_text("x")

        async with TestClient(TestServer(_make_app(str(repo)))) as client:
            resp = await client.get(f"/api/project/tree?path={repo}")
            data = await resp.json()

        assert data["repo"] is True
        assert data["directories"] == ["p"]
        assert data["paths"] == [".gitignore", "a.txt"]
        assert data["truncated"] is True
        assert data["truncatedDirectories"] == ["", "p"]
