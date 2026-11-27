"""The Bond home and the commands people use on it: bond new, add, link, grant and workspaces, and bond mcp by name."""

from __future__ import annotations

from pathlib import Path

import pytest

from bond import Bond, BondError
from bond.cli import main, mcp_workspaces, parse
from bond.local import DECISIONS, ENV, Home, default_home, is_path
from conftest import FIXTURES


@pytest.fixture
def home(tmp_path) -> Home:
    """An empty home, which commands are pointed at with --home (as BOND_HOME would)."""
    return Home(tmp_path / "home")


def run(home: Home, *argv: str) -> int:
    """Run a bond command against home."""
    command, *rest = argv
    return main([command, *rest, "--home", str(home.root)])


def files(tmp_path: Path, **contents: str) -> dict[str, Path]:
    """Files to add, by name, in a folder of their own (outside the home, as people's files are)."""
    folder = tmp_path / "Downloads"
    folder.mkdir(exist_ok=True)
    for name, text in contents.items():
        (folder / name.replace("_", ".")).write_bytes(text.encode())  # exactly these bytes, on Windows too
    return {name.replace("_", "."): folder / name.replace("_", ".") for name in contents}


def test_bond_home_is_the_platform_app_data_unless_moved(tmp_path):
    assert default_home({ENV: str(tmp_path / "elsewhere")}, "linux") == tmp_path / "elsewhere"
    assert default_home({}, "darwin") == Path.home() / "Library" / "Application Support" / "Bond"
    assert default_home({"LOCALAPPDATA": str(tmp_path / "local")}, "win32") == tmp_path / "local" / "Bond"
    assert default_home({"XDG_DATA_HOME": str(tmp_path / "data")}, "linux") == tmp_path / "data" / "bond"
    assert default_home({}, "linux") == Path.home() / ".local" / "share" / "bond"


def test_a_value_with_a_separator_is_a_directory_and_anything_else_a_name(home):
    assert is_path("./smith") and is_path("/srv/bond/smith") and is_path("cases/smith")
    assert not is_path("smith") and not is_path("Smith, J (2026)")
    assert home.resolve("smith") == home.root / "workspaces" / "smith"
    assert home.resolve("./smith") == Path("./smith")
    for bad, why in (("", "blank"), (".", "start with '.'"), ("..", "start with '.'"), (".hidden", "start with '.'")):
        with pytest.raises(BondError, match=why):
            home.path(bad)


def test_new_makes_a_workspace_named_as_given_and_never_twice(home, capsys):
    assert run(home, "new", "smith") == 0
    assert home.names() == ["smith"]
    with Bond.local(home.path("smith"), create=False) as bond:
        assert bond.workspace.name == "smith"
    assert run(home, "new", "smith") == 1
    assert "already a workspace named 'smith'" in capsys.readouterr().err


def test_add_copies_files_in_as_sources_named_after_them_with_where_they_came_from(home, tmp_path, capsys):
    run(home, "new", "smith")
    given = files(tmp_path, fax_md="# Fax\n\nCeiling leak for six weeks.\n", notes_md="Letters twice weekly.\n")
    assert run(home, "add", "smith", str(given["fax.md"]), str(given["notes.md"])) == 0
    assert "added" in capsys.readouterr().out
    given["fax.md"].write_text("Changed after it was added.", encoding="utf-8")  # the copy in Bond stays as added
    with Bond.local(home.path("smith"), create=False) as bond:
        assert [d.name for d in bond.documents()] == ["fax.md", "notes.md"]
        fax = bond.filing.document("fax.md")
        assert fax is not None and bond.blob(fax.head) == b"# Fax\n\nCeiling leak for six weeks.\n"
        [entry] = bond.history("fax.md")
        assert entry.origin == str(given["fax.md"].resolve())


def test_add_refuses_a_taken_name_unless_told_how_to_treat_it(home, tmp_path, capsys):
    run(home, "new", "smith")
    first = files(tmp_path, fax_md="First fax.\n")["fax.md"]
    assert run(home, "add", "smith", str(first)) == 0
    assert run(home, "add", "smith", str(first)) == 1
    assert "already has a document named 'fax.md'" in capsys.readouterr().err
    first.write_text("Second fax.\n", encoding="utf-8")
    assert run(home, "add", "smith", str(first), "--as", "fax-2.md") == 0
    assert run(home, "add", "smith", str(first), "--update") == 0
    assert run(home, "add", "smith", str(first), "--update") == 0  # the same bytes again: nothing new
    assert "nothing added" in capsys.readouterr().out
    with Bond.local(home.path("smith"), create=False) as bond:
        assert [d.name for d in bond.documents()] == ["fax.md", "fax-2.md"]
        assert len(bond.history("fax.md")) == 2


def test_add_checks_every_file_before_adding_any(home, tmp_path, capsys):
    run(home, "new", "smith")
    given = files(tmp_path, fax_md="Fax.\n", letter_docx="not really a docx")
    assert run(home, "add", "smith", str(given["fax.md"]), str(given["letter.docx"])) == 1
    assert "Bond can't read letter.docx" in capsys.readouterr().err
    assert run(home, "add", "smith", str(given["fax.md"]), str(tmp_path / "missing.md")) == 1
    assert "not a file" in capsys.readouterr().err
    assert run(home, "add", "smith", str(given["fax.md"]), str(given["fax.md"]), "--as", "x.md") == 1
    assert "--as names one file" in capsys.readouterr().err
    with Bond.local(home.path("smith"), create=False) as bond:
        assert bond.documents() == []


def test_add_needs_a_workspace_that_exists(home, tmp_path, capsys):
    fax = files(tmp_path, fax_md="Fax.\n")["fax.md"]
    assert run(home, "add", "jones", str(fax)) == 1
    err = capsys.readouterr().err
    assert "no workspace named 'jones'" in err and "create it with: bond new jones" in err
    assert not home.path("jones").exists()


def test_add_says_a_pdf_cant_be_cited_until_transcribed(home, tmp_path, capsys):
    run(home, "new", "smith")
    pdf = Path(__file__).parent.parent / "fixtures" / "fax_case_001.pdf"
    assert run(home, "add", "smith", str(pdf)) == 0
    out = capsys.readouterr().out
    assert "PDF has no text of its own: it can't be searched or cited until a document transcribes it" in out
    assert "transcribe it with: bond transcribe" in out


def test_links_are_kept_in_the_home_and_need_both_workspaces(home, capsys):
    run(home, "new", "smith"), run(home, "new", "criteria")
    assert run(home, "link", "smith", "--to", "nowhere") == 1
    assert "no workspace named 'nowhere'" in capsys.readouterr().err
    assert run(home, "link", "smith", "--to", "smith") == 1
    assert run(home, "link", "smith", "--to", "criteria") == 0
    assert run(home, "link", "smith", "--to", "criteria") == 0  # again: already linked
    assert home.links("smith") == ["criteria"] and home.links("criteria") == []
    assert Home(home.root).links("smith") == ["criteria"] and (home.root / DECISIONS).is_file()  # kept on disk
    capsys.readouterr()
    assert run(home, "workspaces") == 0
    assert capsys.readouterr().out == "criteria\nsmith  (links to: criteria)\n"
    assert run(home, "link", "smith", "--to", "criteria", "--remove") == 0
    assert home.links("smith") == []


def test_links_never_loop(home, capsys):
    for name in ("smith", "criteria", "public"):
        run(home, "new", name)
    run(home, "link", "smith", "--to", "criteria")
    run(home, "link", "criteria", "--to", "public")
    assert home.reaches("smith", "public") and not home.reaches("public", "smith")
    capsys.readouterr()
    assert run(home, "link", "public", "--to", "smith") == 1
    assert "'smith' already links to 'public', directly or through others; links can't loop" in capsys.readouterr().err
    assert home.links("public") == []


def test_mcp_serves_workspaces_by_name_with_what_they_link_to(home, tmp_path):
    for name in ("smith", "jones", "criteria"):
        run(home, "new", name)
    run(home, "add", "criteria", str(files(tmp_path, mp112_md="Six weeks of written notice.\n")["mp112.md"]))
    run(home, "link", "smith", "--to", "criteria")
    with mcp_workspaces(parse(["mcp", "--workspace", "smith", "--workspace", "jones", "--home", str(home.root)])) as served:
        assert served.names() == ["smith", "jones"]
        [criteria] = served.open("smith").links()
        assert criteria.workspace.name == "criteria" and served.open("jones").links() == []
        assert [d.name for d in criteria.documents()] == ["mp112.md"]
        run(home, "link", "jones", "--to", "criteria")  # a link made while serving counts from the next call
        assert [b.workspace.name for b in served.open("jones").links()] == ["criteria"]


def test_mcp_never_creates_a_workspace_by_name(home, capsys):
    assert run(home, "mcp", "--workspace", "smith") == 1
    assert "no workspace named 'smith'" in capsys.readouterr().err
    assert home.names() == []


def test_mcp_opens_a_workspace_once_however_often_it_is_named(home, tmp_path):
    run(home, "new", "smith")
    with mcp_workspaces(parse(["mcp", "--workspace", "smith", "--link", str(home.path("smith")), "--home", str(home.root)])) as served:
        assert served.open("smith").links() == []  # never linked to itself
    with pytest.raises(BondError, match="two workspaces given are named 'smith'"):
        with mcp_workspaces(parse(["mcp", "--workspace", "smith", "--workspace", str(home.path("smith")) + "/", "--home", str(home.root)])):
            pass


def test_bond_grant_lets_a_configuration_work_in_a_workspace_until_revoked(home, capsys):
    run(home, "new", "smith")
    assert run(home, "grant", "nowhere", "--to", "desktop") == 1
    assert "no workspace named 'nowhere'" in capsys.readouterr().err
    assert run(home, "grant", "smith", "--to", "desktop") == 0
    capsys.readouterr()
    assert run(home, "workspaces") == 0 and capsys.readouterr().out == "smith  (granted to: desktop)\n"
    with mcp_workspaces(parse(["mcp", "--grants", "desktop", "--home", str(home.root)])) as served:
        assert served.names() == ["smith"] and served.open("smith").workspace.name == "smith"
    with mcp_workspaces(parse(["mcp", "--grants", "claude-code", "--home", str(home.root)])) as served:
        assert served.names() == []
    assert run(home, "grant", "smith", "--to", "desktop", "--remove") == 0
    assert home.granted("desktop") == [] and home.grantees("smith") == []

def test_grant_mode_takes_its_workspaces_from_grants_alone(home, capsys):
    with pytest.raises(SystemExit):
        parse(["mcp", "--grants", "desktop", "--workspace", "smith"])
    with pytest.raises(SystemExit):
        parse(["mcp", "--grants", "desktop", "--link", "./policies"])
    assert "--link goes with --workspace" in capsys.readouterr().err


def test_bond_transcribe_reads_a_pdfs_text_layer_or_records_a_producers_output(home, capsys):
    run(home, "new", "smith")
    run(home, "add", "smith", str(FIXTURES / "fax_case_001.pdf"))
    capsys.readouterr()
    assert run(home, "transcribe", "smith", "fax_case_001.pdf") == 0
    assert run(home, "transcribe", "smith", "fax_case_001.pdf") == 0
    assert capsys.readouterr().out.splitlines() == [
        "transcribed 'fax_case_001.pdf' as 'fax_case_001.pdf.pypdf'",
        "'fax_case_001.pdf.pypdf' already is this transcription; nothing written"]
    assert run(home, "transcribe", "smith", "fax_case_001.pdf", "--with", "reducto") == 1
    assert "Bond doesn't run reducto on pdf; run it, and record its output with --with reducto --output FILE" in capsys.readouterr().err
    assert run(home, "transcribe", "smith", "fax_case_001.pdf", "--with", "reducto", "--output",
               str(FIXTURES / "fax_case_001.reducto.json")) == 0
    assert run(home, "transcribe", "smith", "fax_case_001.pdf", "--with", "acme") == 1
    assert "doesn't know producer 'acme'" in capsys.readouterr().err
    assert run(home, "transcribe", "smith", "fax_case_001.pdf", "--output", str(FIXTURES / "fax_case_001.reducto.json")) == 1
    assert "name the producer with --with" in capsys.readouterr().err
    assert run(home, "transcribe", "smith", "fax_case_001.pdf.pypdf") == 1
    assert "which has text of its own" in capsys.readouterr().err
    with Bond.local(home.path("smith"), create=False) as smith:
        assert [d.name for d in smith.documents()] == [
            "fax_case_001.pdf", "fax_case_001.pdf.pypdf", "fax_case_001.pdf.reducto"]
