"""bond view and bond mcp: which workspaces of the home they open, and that serving one never creates it."""

from __future__ import annotations

import pytest

from bond import Bond, BondError, Format
from bond.cli import _is_loopback, main, mcp_served, parse, view_workspaces
from bond.local import Home
from bond.storage.sqlite import SQLiteStore
from conftest import POSTGRES, postgres_pool


def test_view_opens_a_workspace_in_the_home_without_creating_one(tmp_path, capsys):
    home = Home(tmp_path / "home")
    assert main(["view", "--workspace", "store", "--home", str(home.root), "--no-browser"]) == 1
    assert "no workspace named 'store'" in capsys.readouterr().err
    assert home.names() == []
    home.create("store")
    with home.open("store") as bond:
        bond.ingest("fax", b"Fax.", format=Format.MARKDOWN)
    with view_workspaces(parse(["view", "--workspace", "store", "--home", str(home.root)])) as offered:
        assert offered.names() == ["store"] and [d.name for d in offered.open("store").documents()] == ["fax"]


def test_the_cli_serves_local_workspaces_only(tmp_path):
    """A deployment on its own stores serves them from Python (bond.mcp.server, bond.http.server), not the CLI."""
    for argv in (["view", "--postgres", "postgresql://x"], ["mcp", "--postgres", "postgresql://x"],
                 ["view", "--workspace", str(tmp_path), "--s3-bucket", "b"]):
        with pytest.raises(SystemExit) as e:
            main(argv)
        assert e.value.code == 2, argv


def test_a_store_is_created_only_when_asked(tmp_path):
    with pytest.raises(BondError, match="no Bond store"):
        SQLiteStore(tmp_path / "bond.db", create=False)
    assert not (tmp_path / "bond.db").exists()
    SQLiteStore(tmp_path / "bond.db").close()
    SQLiteStore(tmp_path / "bond.db", create=False).close()


@pytest.mark.skipif(not POSTGRES, reason="BOND_TEST_POSTGRES unset")
def test_a_postgres_store_is_created_only_when_asked(tmp_path):
    from bond.storage.postgres import PostgresStore

    with postgres_pool() as pool:
        with pytest.raises(BondError, match="holds no Bond store"):
            PostgresStore(pool, create=False)
        with pool.connection() as conn:
            assert conn.execute("SELECT count(*) FROM pg_tables WHERE schemaname = current_schema()").fetchone() == (0,)
        PostgresStore(pool)
        assert list(PostgresStore(pool, create=False).list(())) == []


def test_hosts():
    assert _is_loopback("127.0.0.1") and _is_loopback("localhost") and _is_loopback("::1")
    assert not _is_loopback("0.0.0.0") and not _is_loopback("example.com")


def test_a_local_workspace_is_named_after_its_directory(tmp_path):
    with Bond.local(tmp_path / "case-001") as bond:
        assert bond.workspace.name == "case-001"
    with Bond.local(tmp_path / "case-001", create=False) as again:
        assert again.workspace == bond.workspace


def test_mcp_serves_a_workspace_linked_to_what_it_may_cite(tmp_path):
    home = Home(tmp_path / "home")
    for name in ("policies", "case-001"):
        home.create(name)
    with home.open("policies") as policies:
        policies.ingest("policy", b"Six weeks.", format=Format.MARKDOWN)
    at_home = ["--home", str(home.root)]
    with mcp_served(parse(["mcp", "--workspace", "case-001", "--link", "policies", *at_home])) as served:
        [name] = served.workspaces.names()
        tools = served.tools(name)
        assert (name, tools.bond.workspace.name, tools.bond.documents()) == ("case-001", "case-001", [])
        assert [b.workspace.name for b in tools.bond.links()] == ["policies"]
        assert [d.document for d in tools.documents("policies")] == ["policy"]
        [hit] = tools.search("Six weeks")
        assert hit.workspace == "policies"
    with mcp_served(parse(["mcp", "--workspace", "case-001", *at_home])) as served:
        assert served.tools("case-001").bond.links() == []


def test_mcp_needs_workspaces_that_exist(tmp_path, capsys):
    home = Home(tmp_path / "home")
    assert main(["mcp", "--workspace", "nothing", "--home", str(home.root)]) == 1
    assert "bond mcp: no workspace named 'nothing'" in capsys.readouterr().err
    home.create("case")
    assert main(["mcp", "--workspace", "case", "--link", "nowhere", "--home", str(home.root)]) == 1
    assert "bond mcp: no workspace named 'nowhere'" in capsys.readouterr().err
    assert home.names() == ["case"]


def test_mcp_refuses_mixed_up_arguments(tmp_path, capsys):
    for argv, problem in (
        (["mcp"], "one of the arguments --workspace --grants is required"),
    ):
        with pytest.raises(SystemExit) as e:
            parse(argv)
        assert e.value.code == 2 and problem in capsys.readouterr().err, argv


def test_view_offers_the_workspaces_named_or_else_the_whole_home(tmp_path, capsys):
    home = Home(tmp_path / "home")
    for name in ("case-001", "case-003", "policies"):
        home.create(name)
    at_home = ["--home", str(home.root)]
    with view_workspaces(parse(["view", *at_home])) as offered:
        assert offered.names() == ["case-001", "case-003", "policies"]
    with view_workspaces(parse(["view", *at_home, "--workspace", "case-003", "--workspace", "policies"])) as offered:
        assert offered.names() == ["case-003", "policies"]
    assert main(["view", "--home", str(tmp_path / "empty"), "--no-browser"]) == 1
    assert "no workspaces in" in capsys.readouterr().err
    assert main(["view", *at_home, "--workspace", "case-002", "--no-browser"]) == 1
    assert "no workspace named 'case-002'" in capsys.readouterr().err
    assert not home.exists("case-002")
