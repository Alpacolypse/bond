"""The workspaces example runs, and each case cites the policies it links to, and its determination verifies."""

from __future__ import annotations

import pytest
import workspaces

from bond import Bond


def test_the_example_builds_a_policies_workspace_and_a_workspace_per_case(tmp_path, capsys):
    assert workspaces.main(["--root", str(tmp_path / "ws")]) == 0
    out = capsys.readouterr().out
    assert ("the agent wrote a determination, every word accounted for (partially_supported); verify: intact; "
            "its trace runs through case-001, policies") in out
    with Bond.local(tmp_path / "ws" / "policies", create=False) as policies:
        for name in workspaces.CASES:
            with Bond.local(tmp_path / "ws" / name, create=False, links=[policies]) as case:
                assert case.workspace.name == name
                assert [d.name for d in case.documents()] == ["fax_case_001" if name == "case-001" else "fax_case_003",
                                                              f"{workspaces.CASES[name][0]}.reducto", "determination"]
                determination = case.document("determination").head
                assert case.verify(determination) == []
                case.set_links([])
                assert {i.code.value for i in case.verify(determination)} == {"unreachable"}


def test_the_example_rebuilds_only_its_own_workspaces(tmp_path):
    assert workspaces.main(["--root", str(tmp_path / "ws")]) == 0
    with pytest.raises(SystemExit, match="not empty"):
        workspaces.main(["--root", str(tmp_path / "ws")])
    assert workspaces.main(["--root", str(tmp_path / "ws"), "--force"]) == 0
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "notes.txt").write_text("keep me")
    with pytest.raises(SystemExit, match="refusing to delete"):
        workspaces.main(["--root", str(tmp_path / "other"), "--force"])
    assert (tmp_path / "other" / "notes.txt").exists()
