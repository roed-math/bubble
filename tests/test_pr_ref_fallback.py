"""A PR whose base repository lacks refs/pull/N/head falls back to the head branch."""

import subprocess
from unittest.mock import patch

from bubble import cli
from bubble.target import Target


def _pr(ref="11891"):
    return Target(owner="o", repo="r", kind="pr", ref=ref, original=f"o/r/pull/{ref}")


def _missing_ref(*_args, **_kwargs):
    raise subprocess.CalledProcessError(128, ["git", "fetch"])


def test_missing_pull_ref_fetches_the_head_branch(tmp_data_dir, capsys):
    meta = ("feature", "fork/r", "https://github.com/fork/r.git")
    with (
        patch("bubble.cli.init_bare_repo", return_value=tmp_data_dir / "r.git"),
        patch("bubble.cli.fetch_ref", side_effect=_missing_ref),
        patch("bubble.cli.fetch_pr_metadata", return_value=meta),
        patch("bubble.cli.fetch_pr_head_branch") as fetch_head,
    ):
        cli._resolve_ref_source(_pr(), no_clone=False)
    fetch_head.assert_called_once_with("o/r", "11891", "https://github.com/fork/r.git", "feature")
    out = capsys.readouterr().out
    assert "fetched fork/r:feature in its place" in out
    assert "could not prefetch" not in out


def test_warns_when_the_head_branch_is_also_unavailable(tmp_data_dir, capsys):
    with (
        patch("bubble.cli.init_bare_repo", return_value=tmp_data_dir / "r.git"),
        patch("bubble.cli.fetch_ref", side_effect=_missing_ref),
        patch("bubble.cli.fetch_pr_metadata", return_value=None),
        patch("bubble.cli.fetch_pr_head_branch") as fetch_head,
    ):
        cli._resolve_ref_source(_pr(), no_clone=False)
    fetch_head.assert_not_called()
    assert "could not prefetch PR ref" in capsys.readouterr().out


def test_present_pull_ref_does_not_query_the_pr(tmp_data_dir):
    with (
        patch("bubble.cli.init_bare_repo", return_value=tmp_data_dir / "r.git"),
        patch("bubble.cli.fetch_ref"),
        patch("bubble.cli.fetch_pr_metadata") as meta,
    ):
        cli._resolve_ref_source(_pr(), no_clone=False)
    meta.assert_not_called()
