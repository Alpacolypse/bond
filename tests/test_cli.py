"""bond view and bond mcp: which workspaces they open, and that viewing one never creates it; bond import."""

from __future__ import annotations

import pytest

from bond import Bond, BondError, Format
from bond.cli import _is_loopback, main, mcp_served, parse, view_workspaces
from bond.local import Home
from bond.storage.sqlite import SQLiteCatalog
from conftest import POSTGRES, postgres_pool


def test_view_opens_a_local_workspace_without_creating_one(tmp_path, capsys):
    assert main(["view", "--workspace", str(tmp_path / "nothing"), "--no-browser"]) == 1
    assert "no workspace at" in capsys.readouterr().err
    assert not (tmp_path / "nothing").exists()
    with Bond.local(tmp_path / "store") as bond:
        bond.ingest("fax", b"Fax.", format=Format.MARKDOWN)
    with view_workspaces(parse(["view", "--workspace", str(tmp_path / "store")])) as offered:
        assert offered.names() == ["store"] and [d.name for d in offered.open("store").documents()] == ["fax"]


def test_view_refuses_mixed_up_arguments(tmp_path):
    for argv in (["view", "--workspace", str(tmp_path), "--postgres", "postgresql://x"],
                 ["view", "--postgres", "postgresql://x"],
                 ["view", "--postgres", "postgresql://x", "--blobs", "b", "--s3-bucket", "b"],
                 ["view", "--workspace", str(tmp_path), "--s3-bucket", "b"]):
        with pytest.raises(SystemExit) as e:
            main(argv)
        assert e.value.code == 2, argv


def test_a_catalog_is_created_only_when_asked(tmp_path):
    with pytest.raises(BondError, match="no Bond catalog"):
        SQLiteCatalog(tmp_path / "bond.db", create=False)
    assert not (tmp_path / "bond.db").exists()
    SQLiteCatalog(tmp_path / "bond.db").close()
    SQLiteCatalog(tmp_path / "bond.db", create=False).close()


@pytest.mark.skipif(not POSTGRES, reason="BOND_TEST_POSTGRES unset")
def test_view_opens_postgres_with_local_blobs_without_creating_tables(tmp_path):
    from bond.storage.postgres import PostgresCatalog

    with postgres_pool() as pool:
        with pytest.raises(BondError, match="holds no Bond catalog"):
            PostgresCatalog(pool, create=False)
        with pool.connection() as conn:
            assert conn.execute("SELECT count(*) FROM pg_tables WHERE schemaname = current_schema()").fetchone() == (0,)
        PostgresCatalog(pool)
        assert PostgresCatalog(pool, create=False).all_documents() == []


def test_hosts():
    assert _is_loopback("127.0.0.1") and _is_loopback("localhost") and _is_loopback("::1")
    assert not _is_loopback("0.0.0.0") and not _is_loopback("example.com")


def test_a_local_workspace_is_named_after_its_directory(tmp_path):
    with Bond.local(tmp_path / "case-001") as bond:
        assert bond.workspace.name == "case-001"
    with Bond.local(tmp_path / "case-001", create=False) as again:
        assert again.workspace == bond.workspace


def test_import_brings_a_document_from_one_workspace_into_another(tmp_path, capsys):
    with Bond.local(tmp_path / "policies") as policies:
        policies.ingest("policy", b"# IP-112\n\nSix weeks of written notice.\n", format=Format.MARKDOWN)
    argv = ["import", "--from", str(tmp_path / "policies"), "--into", str(tmp_path / "case-001"), "policy"]
    assert main(argv) == 0 and main(argv) == 0  # again: nothing new to bring
    assert "imported 'policy' from 'policies' into 'case-001' as 'policy' with its provenance" in capsys.readouterr().out
    with Bond.local(tmp_path / "case-001") as case:
        [entry] = case.history("policy")
        assert entry.imported is not None and entry.imported.workspace_name == "policies"
    assert main(["import", "--from", str(tmp_path / "nowhere"), "--into", str(tmp_path / "x"), "policy"]) == 1
    assert "no workspace at" in capsys.readouterr().err


def test_mcp_serves_a_local_workspace_with_its_import_sources(tmp_path):
    with Bond.local(tmp_path / "policies") as policies:
        policies.ingest("policy", b"Six weeks.", format=Format.MARKDOWN)
    (tmp_path / "case-001").mkdir()  # an empty directory becomes a new workspace for the agent
    argv = ["mcp", "--workspace", str(tmp_path / "case-001"), "--import-from", str(tmp_path / "policies"),
            "--import-depth", "all", "--import-limit", "7"]
    with mcp_served(parse(argv)) as served:
        [name] = served.workspaces.names()
        tools = served.tools(name)
        assert (name, tools.bond.workspace.name, tools.bond.documents()) == ("case-001", "case-001", [])
        assert list(tools.sources) == ["policies"] and [d.document for d in tools.documents("policies")] == ["policy"]
        assert (tools.import_depth, tools.import_limit) == (None, 7)
    with mcp_served(parse(["mcp", "--workspace", str(tmp_path / "case-001")])) as served:
        tools = served.tools("case-001")
        assert (dict(tools.sources), tools.import_depth, tools.import_limit) == ({}, 0, 100)


def test_mcp_needs_existing_directories(tmp_path, capsys):
    assert main(["mcp", "--workspace", str(tmp_path / "nothing")]) == 1
    assert "bond mcp: no workspace at" in capsys.readouterr().err
    assert not (tmp_path / "nothing").exists()
    (tmp_path / "case").mkdir()
    assert main(["mcp", "--workspace", str(tmp_path / "case"), "--import-from", str(tmp_path / "nowhere")]) == 1
    assert "bond mcp: no workspace at" in capsys.readouterr().err


def test_mcp_refuses_mixed_up_arguments(tmp_path, capsys):
    for argv, problem in (
        (["mcp"], "one of the arguments --workspace --postgres --grants is required"),
        (["mcp", "--postgres", "postgresql://x"], "--postgres needs exactly one of --blobs and --s3-bucket"),
        (["mcp", "--postgres", "postgresql://x", "--blobs", "b", "--s3-bucket", "b"],
         "--postgres needs exactly one of --blobs and --s3-bucket"),
        (["mcp", "--workspace", str(tmp_path), "--blobs", "b"], "go with --postgres"),
        (["mcp", "--workspace", str(tmp_path), "--import-depth", "-1"], "a depth is a number of hops, or 'all'"),
        (["mcp", "--workspace", str(tmp_path), "--import-limit", "0"], "must be a whole number, at least 1"),
    ):
        with pytest.raises(SystemExit) as e:
            parse(argv)
        assert e.value.code == 2 and problem in capsys.readouterr().err, argv


def test_import_depth_is_a_number_of_hops_or_all(tmp_path, capsys):
    root = ["import", "--from", str(tmp_path / "a"), "--into", str(tmp_path / "b"), "doc"]
    assert (parse(root).depth, parse([*root, "--depth", "3"]).depth, parse([*root, "--depth", "all"]).depth) == (0, 3, None)
    with pytest.raises(SystemExit):
        parse([*root, "--depth", "-1"])
    assert "a depth is a number of hops, or 'all'; got '-1'" in capsys.readouterr().err


def test_view_offers_the_workspaces_named_or_else_the_whole_home(tmp_path, capsys):
    home = Home(tmp_path / "home")
    for name in ("case-001", "case-003", "policies"):
        home.create(name)
    with Bond.local(tmp_path / "elsewhere"):
        pass
    at_home = ["--home", str(home.root)]
    with view_workspaces(parse(["view", *at_home])) as offered:
        assert offered.names() == ["case-001", "case-003", "policies"]
    with view_workspaces(parse(["view", *at_home, "--workspace", "case-003", "--workspace",
                                str(tmp_path / "elsewhere")])) as offered:
        assert offered.names() == ["case-003", "elsewhere"]
    with pytest.raises(BondError, match="two workspaces given are named 'case-003'"):
        with view_workspaces(parse(["view", *at_home, "--workspace", "case-003", "--workspace",
                                    str(home.path("case-003")) + "/"])):
            pass
    assert main(["view", "--home", str(tmp_path / "empty"), "--no-browser"]) == 1
    assert "no workspaces in" in capsys.readouterr().err
    assert main(["view", *at_home, "--workspace", "case-002", "--no-browser"]) == 1
    assert "no workspace named 'case-002'" in capsys.readouterr().err
    assert not home.exists("case-002")
