"""The workspaces example runs, and each case comes out a closed world whose determination verifies."""

from __future__ import annotations

import pytest
import workspaces

from bond import Bond


def test_the_example_builds_a_policies_workspace_and_a_workspace_per_case(tmp_path, capsys):
    assert workspaces.main(["--root", str(tmp_path / "ws")]) == 0
    out = capsys.readouterr().out
    assert "the agent wrote a determination, every word accounted for (partially_supported); verify: intact" in out
    for name in workspaces.CASES:
        with Bond.local(tmp_path / "ws" / name, create=False) as case:
            assert case.workspace.name == name and case.frontier() == []
            names = [d.name for d in case.documents()]
            assert "determination" in names and "policy/IP-112" in names
            assert case.verify(case.document("determination").head) == []
            assert case.history("policy/IP-112")[-1].imported is not None
    with Bond.local(tmp_path / "ws" / "case-001") as one, Bond.local(tmp_path / "ws" / "case-003") as three:
        assert one.document("policy/IP-112").head == three.document("policy/IP-112").head  # same id everywhere


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
