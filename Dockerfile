# Container image for the resume chatbot.
#
# Deployed to Google Cloud Run (see CLAUDE.md), which injects $PORT. The image is
# deliberately platform-portable rather than tied to a host: the CMD honours
# $PORT when one is set and falls back to 7860 otherwise.
FROM python:3.11-slim

# The two cache paths are baked into the image rather than left under /tmp.
#
# /tmp is ephemeral on every platform this targets, so pointing the caches there
# meant fastembed re-downloaded bge-small-en-v1.5 (~65 MB) from HuggingFace on
# every single cold start, before the app could serve its first request. On Cloud
# Run, which scales to zero, that is every visitor who arrives after an idle
# period. Prefetching into the image below turns that download into a layer.
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/opt/cache/hf \
    FASTEMBED_CACHE_PATH=/opt/cache/fastembed

WORKDIR /app

# Install dependencies first for better layer caching.
#
# requirements.lock, not requirements.txt: the lockfile is fully pinned, so two
# builds a month apart install the same versions. Installing the ranges directly
# means a transitive release can change what ships with no commit in between.
# requirements.txt is still the source of truth for *which* direct dependencies
# exist and what range each is allowed; the lock is what gets installed.
COPY requirements.lock .
RUN pip install --upgrade pip && pip install -r requirements.lock

# Drop root before the prefetch below, not after it.
#
# Ordering matters here for image size, not just for security. `chown -R` over
# the 65 MB model cache would force overlayfs to copy every one of those files up
# into a new layer, paying for the model a second time; downloading it as the
# user that will read it avoids the chown entirely. Only the empty directory is
# chowned, which is free.
#
# No home directory: HF_HOME and FASTEMBED_CACHE_PATH are set explicitly, so
# nothing looks for one. The uid is high and fixed on purpose, well clear of any
# uid the host assigns and stable across rebuilds.
RUN useradd --no-create-home --uid 10001 appuser \
    && mkdir -p /opt/cache && chown appuser:appuser /opt/cache
USER appuser

# Prefetch the embedding model into the image.
#
# A build-time download so it is not a startup one. Without this, fastembed pulls
# bge-small-en-v1.5 from HuggingFace on every cold start, which on a scale-to-zero
# platform means every visitor after an idle period. It also makes the site's
# ability to boot at all depend on HuggingFace being reachable, which is the
# stronger reason: a third party's outage should not be able to take this down.
#
# Reads the model name from app/config.py rather than repeating the string, so
# changing embedding_model cannot silently leave the wrong model baked in.
# --chown because this runs as appuser: a root-owned COPY could not be cleaned up
# afterwards. Free here, unlike a chown over the model cache, because it is two
# small files landing in a fresh layer rather than a copy-up of an existing one.
COPY --chown=appuser:appuser app/config.py app/__init__.py /tmp/build/app/
RUN cd /tmp/build && python -c "\
from app.config import settings; \
from fastembed import TextEmbedding; \
TextEmbedding(model_name=settings.embedding_model)" && rm -rf /tmp/build

# Copy the application, knowledge base, templates, static frontend, and profile.
# These land root-owned and world-readable, which is what appuser needs and no more.
COPY app/ ./app/
COPY knowledge/ ./knowledge/

# From here on, nothing reaches HuggingFace. The model is in the image already,
# and without this fastembed still asks the Hub about it on every engine build,
# which is a second of network on the first question after each cold start.
ENV HF_HUB_OFFLINE=1 \
    INDEX_DIR=/opt/cache/index

# Embed the corpus into the image, after app/ and knowledge/ and before
# templates/ and static/, so a frontend change does not re-embed.
#
# This was the first question's cost after every cold start: ~40 s of embedding
# on Cloud Run's one vCPU, before Groq was even called. Built here, it is loaded
# in milliseconds. app/ingest.py stamps the index with a hash of the corpus and
# the chunker, so an index that disagrees with the files beside it is refused
# and rebuilt at runtime rather than served. Under amd64 emulation on Apple
# silicon this step takes a while; that is build time, not visitor time.
RUN python -c "\
from app.config import settings; \
from app.ingest import save_index; \
save_index(settings.index_dir)"

COPY templates/ ./templates/
COPY static/ ./static/
COPY profile.json ./profile.json

# The Groq API key is injected at runtime as a platform secret (never baked in).
EXPOSE 7860

# Listen on $PORT when the platform sets one, 7860 otherwise. Cloud Run injects
# PORT and requires the container to honour it. Shell form is required here: the
# exec form does not expand a variable, so the port would arrive at uvicorn as
# the literal string "${PORT}".
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860}"]
