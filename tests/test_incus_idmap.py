"""Native Incus: map the operator's uid/gid onto the container's `user` (ids read from the stopped
container's /etc/passwd, not assumed), only when /etc/subuid and /etc/subgid allow it; print a
hint and start unmapped otherwise; start unmapped when Incus refuses; never on a configured
remote; and mount plainly (no shift) once a mapping is active."""

from bubble.runtime import incus as I

PASSWD = (
    "root:x:0:0:root:/root:/bin/bash\n"
    "ubuntu:x:1000:1000::/home/ubuntu:/bin/bash\n"
    "user:x:1001:1001::/home/user:/bin/bash\n"
)


class FakeRuntime(I.IncusRuntime):
    def __init__(self, refuse_idmap=False, remote=""):
        super().__init__(remote=remote)
        self.calls = []
        self.refuse_idmap = refuse_idmap

    def _run(self, args, check=True, capture=True):
        self.calls.append(list(args))
        if args[0] == "file" and args[1] == "pull":
            return PASSWD
        if args[0] == "config" and args[1] == "set" and self.refuse_idmap:
            raise I.IncusError(
                1, ["incus", *args], "", "Error: Invalid idmap: uid not allowed by subuid"
            )
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


def _kinds(rt):
    return [c[0] if c[0] != "config" else "config-set" for c in rt.calls]


def test_maps_onto_the_real_container_user_when_allowed(monkeypatch, tmp_path, capsys):
    _subid(monkeypatch, tmp_path, True, True)
    rt = FakeRuntime()
    rt.launch("c", "img")
    assert _kinds(rt) == ["init", "file", "config-set", "start"]
    assert rt.calls[2] == ["config", "set", "c", "raw.idmap=uid 1005 1001\ngid 1005 1001"]
    assert rt._idmap_active is True
    assert "bubble:" not in capsys.readouterr().out
    rt.add_disk("c", "d", "/host/x", "/opt/x", readonly=True)
    assert rt.calls[-1] == [
        "config",
        "device",
        "add",
        "c",
        "d",
        "disk",
        "source=/host/x",
        "path=/opt/x",
        "readonly=true",
    ]


def test_hint_and_unmapped_when_not_allowed(monkeypatch, tmp_path, capsys):
    _subid(monkeypatch, tmp_path, False, True)
    rt = FakeRuntime()
    rt.launch("c", "img")
    assert _kinds(rt) == ["init", "file", "start"]
    assert rt._idmap_active is False
    out = capsys.readouterr().out
    assert "root:1005:1" in out and "/etc/subuid" in out
    rt.add_disk("c", "d", "/host/x", "/opt/x")
    assert rt.calls[-1][-1] == "shift=true"


def test_starts_unmapped_when_incus_refuses(monkeypatch, tmp_path, capsys):
    _subid(monkeypatch, tmp_path, True, True)
    rt = FakeRuntime(refuse_idmap=True)
    rt.launch("c", "img")
    assert _kinds(rt) == ["init", "file", "config-set", "start"]
    assert rt._idmap_active is False
    assert "restart incus" in capsys.readouterr().out


def test_no_mapping_when_operator_is_the_container_user(monkeypatch, tmp_path):
    _subid(monkeypatch, tmp_path, True, True, uid=1001, gid=1001)
    rt = FakeRuntime()
    rt.launch("c", "img")
    assert _kinds(rt) == ["init", "file", "start"]


def test_no_mapping_on_a_configured_remote(monkeypatch, tmp_path):
    _subid(monkeypatch, tmp_path, True, True)
    rt = FakeRuntime(remote="bubble-colima")
    rt.launch("c", "img")
    assert _kinds(rt) == ["launch"]
    assert not any(a.startswith("raw.idmap=") for c in rt.calls for a in c)


def test_subid_parser(tmp_path):
    f = tmp_path / "subuid"
    f.write_text("# comment\nroot:100000:65536\nalice:200000:65536\n0:1005:1\n")
    assert I.IncusRuntime._subid_allows(str(f), 1005) is True
    assert I.IncusRuntime._subid_allows(str(f), 150000) is True
    assert I.IncusRuntime._subid_allows(str(f), 250000) is False
    assert I.IncusRuntime._subid_allows(str(tmp_path / "missing"), 1) is None
