"""Native Incus: map the operator's uid/gid onto the container user at launch (raw.idmap), only when
/etc/subuid and /etc/subgid allow it, with a hint and an unmapped launch otherwise, and an unmapped
relaunch when Incus itself refuses the mapping."""

import subprocess

import pytest

from bubble.runtime import incus as I


class FakeRuntime(I.IncusRuntime):
    def __init__(self, refuse_idmap=False):
        super().__init__(remote="")
        self.calls = []
        self.refuse_idmap = refuse_idmap

    def _run(self, args, check=True, capture=True):
        self.calls.append(list(args))
        if args[0] == "launch" and self.refuse_idmap and any(a.startswith("raw.idmap=") for a in args):
            raise I.IncusError(1, ["incus", *args], "", "Error: Invalid idmap: uid not allowed by subuid")
        return ""

    def _get_info(self, name):
        return name


def _subid(monkeypatch, tmp_path, uid_ok, gid_ok, uid=1005, gid=1005):
    su = tmp_path / "subuid"
    sg = tmp_path / "subgid"
    su.write_text("root:1000000:1000000000\n" + (f"root:{uid}:1\n" if uid_ok else ""))
    sg.write_text("root:1000000:1000000000\n" + (f"root:{gid}:1\n" if gid_ok else ""))
    real_allows = I.IncusRuntime._subid_allows

    def allows(path, wanted):
        return real_allows(str(su) if path.endswith("subuid") else str(sg), wanted)

    monkeypatch.setattr(I.IncusRuntime, "_subid_allows", staticmethod(allows))
    monkeypatch.setattr(I.os, "getuid", lambda: uid)
    monkeypatch.setattr(I.os, "getgid", lambda: gid)


def test_maps_when_allowed(monkeypatch, tmp_path, capsys):
    _subid(monkeypatch, tmp_path, True, True)
    rt = FakeRuntime()
    rt.launch("c", "img")
    launch = [c for c in rt.calls if c[0] == "launch"]
    assert len(launch) == 1
    assert launch[0][-2:] == ["-c", "raw.idmap=uid 1005 1000\ngid 1005 1000"]
    assert "cannot own" not in capsys.readouterr().err


def test_hint_and_unmapped_when_not_allowed(monkeypatch, tmp_path, capsys):
    _subid(monkeypatch, tmp_path, False, True)
    rt = FakeRuntime()
    rt.launch("c", "img")
    launch = [c for c in rt.calls if c[0] == "launch"]
    assert launch == [["launch", "img", "c"]]
    err = capsys.readouterr().err
    assert "root:1005:1" in err and "/etc/subuid" in err


def test_relaunches_unmapped_when_incus_refuses(monkeypatch, tmp_path, capsys):
    _subid(monkeypatch, tmp_path, True, True)
    rt = FakeRuntime(refuse_idmap=True)
    rt.launch("c", "img")
    kinds = [c[0] for c in rt.calls]
    assert kinds == ["launch", "delete", "launch"]
    assert not any(a.startswith("raw.idmap=") for a in rt.calls[-1])
    assert "cannot own" in capsys.readouterr().err


def test_no_mapping_when_uid_is_already_the_container_user(monkeypatch, tmp_path):
    _subid(monkeypatch, tmp_path, True, True, uid=1000, gid=1000)
    rt = FakeRuntime()
    rt.launch("c", "img")
    assert rt.calls == [["launch", "img", "c"]]


def test_subid_parser():
    import tempfile, os
    with tempfile.NamedTemporaryFile("w", delete=False) as f:
        f.write("# comment\nroot:100000:65536\nalice:200000:65536\n0:1005:1\n")
    try:
        assert I.IncusRuntime._subid_allows(f.name, 1005) is True
        assert I.IncusRuntime._subid_allows(f.name, 150000) is True
        assert I.IncusRuntime._subid_allows(f.name, 250000) is False
        assert I.IncusRuntime._subid_allows("/nonexistent/subuid", 1) is None
    finally:
        os.unlink(f.name)
