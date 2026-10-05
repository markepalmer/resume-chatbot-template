"""The committed Career Arc SVG is what the current profile.json produces.

`static/career_arc.svg` is a build artifact, inlined into the page. Editing the
`career` block without rerunning `tools/build_career_arc.py` leaves the page
showing the previous run's geometry beside the new legend, and the legend joins
its chips to segments by index, so the mismatch is not merely cosmetic. Nothing
errors and the figure still looks like a figure, which is what makes it a trap.

The builder writes to a path anchored on its own `__file__`, so it cannot be
pointed elsewhere without editing it (and tools/ is not ours to edit). Instead
the inputs are copied into a tmp_path that has the same shape as the repo and
the builder is run there as a subprocess, which leaves the real static/ alone.
"""

import shutil
import subprocess
import sys

import pytest

BUILDER = "build_career_arc.py"
ARTIFACT = "career_arc.svg"


@pytest.fixture
def rebuilt(tmp_path, repo_root) -> bytes:
    sandbox = tmp_path / "repo"
    (sandbox / "tools").mkdir(parents=True)
    (sandbox / "static").mkdir(parents=True)
    shutil.copy2(repo_root / "tools" / BUILDER, sandbox / "tools" / BUILDER)
    shutil.copy2(repo_root / "profile.json", sandbox / "profile.json")

    done = subprocess.run(
        [sys.executable, str(sandbox / "tools" / BUILDER)],
        capture_output=True,
        text=True,
        cwd=str(sandbox),
    )
    assert done.returncode == 0, (
        f"tools/{BUILDER} failed against the current profile.json:\n"
        f"{done.stderr or done.stdout}"
    )
    return (sandbox / "static" / ARTIFACT).read_bytes()


def test_career_arc_svg_is_not_stale(rebuilt, repo_root):
    committed = (repo_root / "static" / ARTIFACT).read_bytes()
    assert committed == rebuilt, (
        f"static/{ARTIFACT} does not match what tools/{BUILDER} produces from the "
        "current profile.json. The figure is a build artifact: rerun "
        "`python3 tools/build_career_arc.py` after any change to the `career` or "
        "`skills` blocks. "
        f"(committed {len(committed)} bytes, rebuilt {len(rebuilt)} bytes)"
    )


def test_career_arc_svg_is_committed(repo_root):
    path = repo_root / "static" / ARTIFACT
    assert path.is_file(), (
        f"static/{ARTIFACT} is missing. load_career_arc_svg returns '' when it is "
        "absent, which hides the whole Career Arc section rather than erroring."
    )
