"""Evaluation harness. Dev tooling: never copied into the container image.

The Dockerfile copies app/, knowledge/, templates/, static/ and profile.json, so
nothing here ships. That is what lets this package import the ``groq`` SDK
directly, which app/ deliberately does not (see the note above
``_is_upstream_throttle`` in app/main.py).
"""
