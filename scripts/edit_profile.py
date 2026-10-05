#!/usr/bin/env python3
"""A local, browser based editor for profile.json.

Run it, edit the form it opens, press Save:

    .venv/bin/python scripts/edit_profile.py

WHY THIS EXISTS ALONGSIDE setup_profile.py
------------------------------------------
Both write the same file and share every rule; they differ in shape, and the
shape is the point. ``setup_profile.py`` is forty sequential prompts, which is
the right tool for a fresh fork with nothing to look at yet. It is the wrong one
for an edit, because you cannot see what you are building, cannot go back, and
find out what a field costs only after the last prompt. Every fault a real run
of it surfaced was of that kind: a fraternity line glued onto a school's label,
a stat rendered "Employees10", career skills naming groups that did not exist.
A form shows all of it at once and validates while you type.

WHY IT IS LOCAL AND NOT PART OF THE SITE
----------------------------------------
The deployed service is read only by design, has no auth, and runs on a
filesystem that does not survive the instance. An edit endpoint there needs a
bucket, a login and cache invalidation before it writes a single field. On a
checkout none of that exists: the file is right there, the generators are a
subprocess away, and the thing being customised is customised before it ships.
So this stays in ``scripts/``, which the Dockerfile does not copy, and it binds
to loopback only.

WHY THERE IS A TOKEN
--------------------
It writes files and runs generators, so it is not a read only toy. Any page open
in the same browser can POST to 127.0.0.1 without being able to read the reply,
which is enough to overwrite profile.json from a tab the person did not open.
The token in the URL closes it: a cross origin page cannot read it, so it cannot
send it. Same reason the Origin check below rejects rather than ignores.

NOTHING HERE IS A SECOND SOURCE OF TRUTH
----------------------------------------
Validation, the masthead menu, the figure builders and the knowledge scaffold are
imported from ``setup_profile`` rather than restated. They print rather than
return, so their output is captured; a wrapper that reworded them would be a
second copy of the rules, which is what this repo pays for twice already.
"""
from __future__ import annotations

import contextlib
import io
import json
import secrets
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.setup_profile import (  # noqa: E402
    DEFAULT_MASTHEAD_FORMS,
    KINDS,
    MASTHEAD_FORMS,
    MAX_SKILL_GROUPS,
    check_career_order,
    check_resume_dates,
    report_duplicate_skills,
    report_orphan_skills,
    run_builders,
)

PROFILE_PATH = ROOT / "profile.json"
PAGE_PATH = Path(__file__).resolve().parent / "edit_profile.html"
HOST = "127.0.0.1"
PORT = 8765
TOKEN = secrets.token_urlsafe(16)


def captured(fn, *args) -> str:
    """Run one of setup_profile's reporters and return what it printed.

    They report to a terminal by printing. Capturing that is deliberately
    preferred to giving each one a structured return: the rules would then exist
    once for the prompts and once for the form, and the pair would drift on the
    first change. The text is shown as text.
    """
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(*args)
    return buf.getvalue().strip()


def validate(profile: dict) -> list[dict]:
    """Every check the CLI runs after writing, run before writing instead."""
    career = profile.get("career") or []
    skills = profile.get("skills") or {}
    checks = [
        ("Career order", check_career_order, (career,)),
        ("Resume dates", check_resume_dates, (career,)),
        ("Duplicate skills", report_duplicate_skills, (skills,)),
        ("Orphan skills", report_orphan_skills, (career, skills)),
    ]
    out = []
    for label, fn, args in checks:
        try:
            text = captured(fn, *args)
        except Exception as exc:  # a malformed draft must not kill the editor
            text = f"Could not run this check against the current draft: {exc}"
        if text:
            out.append({"label": label, "text": text})
    return out


def save(profile: dict) -> dict:
    """Write profile.json, then rebuild the figures that read it.

    Writing alone leaves the page showing the previous data next to the new
    labels, which is the staleness run_builders exists to prevent, so the two
    are one action here exactly as they are in the CLI.
    """
    # The page creates these keys as it renders (`state.stats ||= []`), so a
    # profile that never had a `stats` key gains an empty one just by being
    # looked at. app/profile.py's _DEFAULTS treats a missing block and an empty
    # one identically, so dropping it is the same page and a clean diff. Unlike
    # the CLI, the editor always shows both sections, so empty here always means
    # the owner has none rather than never-asked.
    for optional in ("stats", "results"):
        if optional in profile and not profile[optional]:
            del profile[optional]

    # ensure_ascii=False, or `·` in bio.meta is rewritten as `·` on save:
    # the same string to a JSON reader, a diff to a human.
    PROFILE_PATH.write_text(
        json.dumps(profile, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return {
        "written": str(PROFILE_PATH.relative_to(ROOT)),
        "builders": captured(run_builders, profile),
        "checks": validate(profile),
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # keep the console for our own output
        pass

    # -- plumbing ----------------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload).encode("utf-8"), "application/json")

    def _authorised(self) -> bool:
        """Token on the request, and no foreign Origin.

        Checked on reads too, not only writes: /api/profile hands back whatever
        is in profile.json, and that is the owner's contact details.
        """
        origin = self.headers.get("Origin")
        if origin and origin not in (f"http://{HOST}:{PORT}", f"http://localhost:{PORT}"):
            return False
        return secrets.compare_digest(self.headers.get("X-Edit-Token", ""), TOKEN)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 4_000_000:
            raise ValueError("empty or oversized body")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    # -- routes ------------------------------------------------------------
    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._send(200, PAGE_PATH.read_bytes(), "text/html; charset=utf-8")
            return
        if not self._authorised():
            self._json(403, {"error": "bad or missing token"})
            return
        if path == "/api/profile":
            self._json(200, {
                "profile": json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
                if PROFILE_PATH.exists() else {},
                "meta": {
                    "masthead_forms": MASTHEAD_FORMS,
                    "default_masthead_forms": DEFAULT_MASTHEAD_FORMS,
                    "kinds": list(KINDS),
                    "max_skill_groups": MAX_SKILL_GROUPS,
                    "path": str(PROFILE_PATH),
                },
            })
            return
        self._json(404, {"error": "no such route"})

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        if not self._authorised():
            self._json(403, {"error": "bad or missing token"})
            return
        try:
            payload = self._body()
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
            return
        profile = payload.get("profile")
        if not isinstance(profile, dict):
            self._json(400, {"error": "no profile object in the request"})
            return
        if path == "/api/validate":
            self._json(200, {"checks": validate(profile)})
            return
        if path == "/api/save":
            try:
                self._json(200, save(profile))
            except Exception as exc:
                self._json(500, {"error": f"{type(exc).__name__}: {exc}"})
            return
        self._json(404, {"error": "no such route"})


def main() -> None:
    if not PAGE_PATH.exists():
        sys.exit(f"Missing {PAGE_PATH}, which is the editor itself.")
    url = f"http://{HOST}:{PORT}/?token={TOKEN}"
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Editing {PROFILE_PATH}")
    print(f"Open {url}")
    print("Nothing is written until you press Save. Ctrl-C here when you are done.")
    threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped. profile.json holds whatever your last Save wrote.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
