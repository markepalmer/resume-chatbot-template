---
title: Resume Chatbot
emoji: 💬
colorFrom: gray
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
license: mit
---

# Resume Chatbot — an open source, grounded resume website

Build a personal job application website whose centerpiece is a grounded chatbot. It answers a
recruiter's questions using only your real background and declines gracefully when a question falls
outside that material. It is a working LLM application, which is a stronger signal to a technical
reviewer than a static portfolio.

You can make it your own by editing three things. No frontend code required.

> The YAML block above is Hugging Face Space configuration, kept so the repo still works as a Space
> if you have a paid plan. The reference deployment is Google Cloud Run; see Deploy below.

## Make it yours in 3 steps

1. **Edit `profile.json`** — your identity and the whole visible page. Run
   `python3 scripts/setup_profile.py` for an interactive prompt that writes all of it for you:
   name, links, the stats band, highlight cards, your skill groups, your career history, and the
   results chart. It then rebuilds the two generated figures for you, which is the step that is
   easiest to forget by hand. It is safe to re-run: pressing Enter through every prompt leaves
   `profile.json` unchanged, and keys it does not manage are preserved rather than dropped.

   **Start by handing it your resume.** The first thing it asks for is the path to a resume PDF, and
   everything it finds there becomes the default answer to a prompt further down: your name, role,
   email, location, any LinkedIn or GitHub link, a tagline from your summary section, your skill
   groups in your own grouping, your engagements with their dates, and one bullet list per
   engagement for `knowledge/resume.md`. It also offers the same PDF as the file the page's Download
   button serves, with the phone number stripped out of it. In the fast case that is one path and
   then Enter through the rest, and you edit rather than compose. It needs `pypdf`
   (`pip install -r requirements-dev.txt`); leave the path blank to skip it and type everything by
   hand instead.

   Nothing the import finds is written without passing a prompt first, and nothing it puts into
   `knowledge/` is generated prose: the bullets are your own resume lines, lifted verbatim and
   stripped of phone numbers and emails, because that file is public and is what the chatbot reads.
   Read it once anyway. PDF text extraction runs lines together, and an entry dated by bare year
   rather than by month is not found at all.

   **Or edit it in a browser instead:** `python3 scripts/edit_profile.py` opens a local form over
   the same file, with the same checks running as you type rather than after you finish, and the
   same figure rebuild on save. Prefer it once `profile.json` exists, since a change to one field
   does not mean answering forty prompts again, and career skills are checkboxes over your skill
   groups, so an entry cannot name a skill that no group defines. It binds to 127.0.0.1, is not
   part of the deployed site, and is not copied into the image.

   While collecting your career it checks the things that otherwise fail silently: that each
   engagement's kind is one the figure can colour, that dates parse and end after start, that your
   entries are in date order (shading reads as recency by list position, not by date), and it
   reports any skill named in your career that is not in a skill group, since those are kept in the
   data but draw no chord. Keys:
   - `name` / `known_as` — the formal name shown in the header/title, and the informal first name the
     chatbot uses in its heading and answers (for example `"Ada P. Okonkwo"` and `"Ada"`).
   - `role`, `tagline`, `photo`, `location`, `links` — the centered masthead. `email` is optional;
     leave it `""` to keep it off the public page.
   - `highlights` — the project carousel cards (add `"url"` to make a card a clickable blue link).
   - `starter_questions` — the suggestion chips under the chat.
   - `skills` — an object of `"Group Name": ["Skill", ...]`. Each group becomes its own color-coded
     mesh in the animated Skillset network, and the flat list is also the accessible fallback.
2. **Replace the docs in `knowledge/`** — your resume, the roles you are open to, an about-me, and one
   file per project. Everything the chatbot can say comes from these markdown files, and `resume.md`
   is also rendered into the collapsible Resume section at the bottom of the page (so the printed
   resume and the chatbot can never drift apart). To share a project's repository, put a
   `Repository: https://...` line near the top of that project's doc. Keep contact details you do not
   want public (phone, personal email) out of `resume.md` — that file is rendered on the page.

   **The shape of a project doc.** These are written *for the chatbot*, not copied out of a project's
   own repository. A README is addressed to someone who has already decided to use your code; this is
   addressed to a recruiter deciding whether to keep reading. The four in `knowledge/projects/` all
   follow the same spine, and chunking splits on headings, so each `##` becomes its own retrievable
   unit and vague headings retrieve badly:

   ```markdown
   # Project: <Name>

   **Source: <solo / team / coursework / client>, <years>. Stack: <technologies>.**

   **Repository (public): https://...** Share this link when someone asks to see the code.

   ## Summary
   Two or three sentences. What it is, who it was for, and what makes it worth mentioning.

   ## What he built            <- one to three sections, named for the thing
   ## What made it rigorous       they describe, not "Details" or "More info"
   ## Findings

   ## Why it matters
   The transferable signal: what this proves you can do on someone else's problem.
   ```

   Rules that are not obvious:

   - **Third person, and facts only.** The bot answers *as* these files. An achievement written here
     is one it will state as fact, so anything you would not defend in an interview does not belong.
   - **One file per project, roughly 800 to 2,200 characters.** Shorter and it loses to other files
     for a retrieval slot; longer and one project can crowd out the rest of your corpus.
   - **Do not leave a heading empty.** An empty section retrieves as a question with silence under it.
   - **Do not write instructions to the model.** See the confidentiality note below.

   **If a project is under NDA**, add a `## Confidentiality` section to that project's file. State
   what is public and what is not, as *facts*, and let the prompts handle the behaviour:

   ```markdown
   ## Confidentiality

   This engagement is confidential.

   Public, and free to discuss: the architecture and the engineering decisions described in this file.

   Confidential, and not published here: client names, deal figures, and internal metrics.
   ```

   Write "these are confidential", never "do not disclose these" or "if asked, say X". `app/prompts.py`
   already tells both models to honour a marking and to answer that the detail is confidential, and it
   does so for every fork. An imperative in the corpus is a runtime instruction sitting in retrievable,
   quotable text, and if it ever surfaces it hands the reader an itemised list of what to probe for.
   The same section is injected on every call (see `always_on_sections` in `profile.json`), because
   a confidentiality marking that has to win a similarity contest before it applies is not a guarantee.

   This is also where to put personal boundaries even if you have no NDA work: **current salary,
   salary expectations, notice period, home address, or your employer's client names.** A resume bot
   that volunteers your salary expectation to a recruiter is the failure this section prevents.
3. **Add your own Groq API key** — create a free key at https://console.groq.com, then set it as the
   `GROQ_API_KEY` secret (a platform secret in production, or a local `.env` file for development).

   **You can do this last, and you should.** The site runs without a key: the page renders, the
   figures draw, and the chat endpoints answer `503 The assistant is not configured: set
   GROQ_API_KEY` until you set one. So do steps 1 and 2, run it, look at your own site, and only then
   decide whether it is worth signing up for anything. The key is yours and is never shared with this
   repo's author; it is read from the environment and never sent to the browser.

That is it. Nothing about you is hardcoded in the application code.

## How it works

```
Frontend (Jinja2 template + static CSS/JS)  -> served by FastAPI
        |
FastAPI backend  (holds GROQ_API_KEY as a secret)
        |
LangChain pipeline
   1. Retrieve: top-k chunks from a FAISS index over knowledge/
   2. Draft:    GPT-OSS 120B on Groq, grounded on retrieved chunks + an always-on profile summary
   3. Judge:    a second Groq pass critiques and repairs the draft against the same context
        |
Host: any platform that runs a container. Reference deployment is Google Cloud Run.
```

Grounding is hybrid: the FAISS retrieval seam handles specific questions, and a compact always-on
summary (from `resume.md` and `roles-open-to.md`) is injected on every call so a retrieval miss cannot
drop core facts. The draft then judge pattern reduces hallucination.

## The page

A single scannable page, chatbot as the centerpiece, rendered from `profile.json` and `knowledge/`:

- **Masthead** — centered name (formal), role, tagline, circular photo, location, and links, over a
  WebGL particle cloud that morphs between the shapes named in `masthead_forms`. Contact fields are
  optional and guarded, so an empty `email` simply drops off.
- **Highlights carousel** — the `highlights` cards with prev/next arrows; a card with a `url` renders
  as a clickable blue-outlined link.
- **Chat** — the grounded assistant. Its heading, placeholder, and answers use `known_as`.
- **Skillset network** — an animated `<canvas>` where each vertex is a skill. Skills are grouped in
  `profile.json`; each group is a color-coded full mesh (every skill in a group links to the others),
  the groups repel and bounce off each other, and the whole field drifts with a subtle scroll
  parallax. A `prefers-reduced-motion` / no-JS fallback shows the skills as a plain chip list, and the
  same list stays in the DOM for screen readers. On a wide screen the panel becomes a **walk**: it
  sticks while a step per discipline scrolls past, each isolating one group and handing it the whole
  panel. The legend below the walk doubles as the filter and a click takes over from the scroll.
- **Career arc** — a chord diagram generated from `career` and `skills`: time along the top, skills
  around the bottom, one chord per engagement-and-skill pair. It is a **walk** on the same terms as
  the network, one engagement per step, with the step's `label`, `detail`, dates and skill list
  beside the figure. Both walks collapse to a compact list under `prefers-reduced-motion`, below
  1040px, and without JavaScript.
- **Measured results** — a generated chart from the `results` block, with the exact numbers in a
  collapsible table beneath it.
- **Resume** — a collapsible section rendered from `knowledge/resume.md`, with a **Download PDF**
  button that uses the browser's print-to-PDF (a print stylesheet hides everything but the resume).

## Project layout

```
profile.json   Your identity + the whole page: name/known_as, role, tagline, photo, links,
               highlight cards, starter questions, the grouped skills network, and the
               masthead cloud's shapes (masthead_forms)
knowledge/     The corpus the chatbot is grounded on (markdown); resume.md also renders the
               Resume section. Edit these to change what it knows and shows.
templates/     The single page, rendered from profile.json + resume HTML (Jinja2)
static/        Stylesheet (styles.css) and the chat + skill-network script (app.js).
               masthead3d.js is the WebGL particle cloud in the header, loaded on demand;
               vendor/ is three.js, vendored so the page never loads script from a CDN
app/           FastAPI app + LangChain pipeline (config, profile, ingest, retriever, prompts, chat, main)
Dockerfile     Builds the container image; honours $PORT, defaults to 7860
requirements.txt   The direct dependencies and the version range each may move within
requirements.lock  Every version pinned. This is what the image installs, see below
```

### Dependencies: two files, and the image installs the lock

`requirements.txt` is the list you edit. `requirements.lock` is fully pinned and is what the
`Dockerfile` actually installs, so that two builds a month apart produce the same image. Ranges alone
are not reproducible: the same `requirements.txt` here resolved `faiss-cpu` 1.15.0 and `fastapi`
0.141.1 inside the container against 1.14.3 and 0.139.2 in a local virtualenv built weeks earlier.

**Adding a dependency to `requirements.txt` alone has no effect on the deployed image.** Add it
there, then regenerate the lock by resolving the ranges in a clean `linux/amd64` container:

```bash
docker run --rm --platform linux/amd64 -v "$PWD":/w -w /w python:3.11-slim \
  sh -c "pip install -q -r requirements.txt && pip freeze" > requirements.lock
```

Note this installs from `requirements.txt`, not from the image: building the image would reinstall
the *existing* lock and your new dependency would never appear. Then rebuild and test before
deploying, since regenerating picks up upstream releases in every range at once.

Resolve it in a container, never in your local virtualenv. The lock has to describe `linux/amd64`,
and a macOS or arm64 environment resolves different versions and wheels that do not exist on the
deploy target.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then put your Groq API key in .env
uvicorn app.main:app --reload --reload-include profile.json --reload-include '*.svg'
```

**Those two `--reload-include` flags are not optional if you are editing your profile.** `uvicorn
--reload` watches `*.py` and nothing else, and `load_profile()` is `@lru_cache(maxsize=1)`, so each
server process reads `profile.json` exactly once. Without them you save a change, refresh the
browser, and see the page built from the file as it was when the server booted. Nothing looks broken,
which is what makes it cost an hour. The same applies to the generated SVGs, hence the second flag.

Local development installs `requirements.txt`, not `requirements.lock`, because the lock pins
`linux/amd64` wheels and you are probably not on that. The consequence is that **your local versions
will drift from the deployed ones**, which is worth remembering when something reproduces in one
place and not the other. The container is the source of truth.

The server starts in about two seconds. The chat engine (the embedding model and the FAISS index) is
built on the first chat request rather than at startup, so the first question is slower than the rest
and the page is served immediately either way. That is deliberate, not a stall.

Open http://localhost:8000 and try a question, or:

```bash
curl -s -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"question":"What are you working on?"}'
```

The page itself uses the streaming variant, which runs the same pipeline but
delivers the final answer token by token as server sent events:

```bash
curl -sN -X POST http://localhost:8000/api/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"question":"What are you working on?"}'
```

Only the judge pass is streamed. The draft is produced in full first and never
reaches the browser, so nothing unverified is ever shown and then rewritten.

## Deploy

The deployable artifact is the `Dockerfile`, so anything that runs a container will host this. The
app listens on `$PORT` when the platform sets one and on 7860 otherwise, which covers both
conventions without a second image.

### Google Cloud Run (the reference deployment)

Free tier covers a personal site comfortably, and it scales to zero, so an idle site costs nothing
and needs no keep-alive pinger. Requires a billing account on file even inside the free tier.

You need two things installed first: the **gcloud CLI** (https://cloud.google.com/sdk/docs/install)
and **Docker** (https://docs.docker.com/get-docker/, Desktop on macOS and Windows). Everything below
assumes both are on your PATH and that Docker is actually running, which is its own easy-to-miss step
on a laptop that just rebooted.

Then pick your two values once and reuse them everywhere below: `PROJECT` is your Google Cloud
project id and `REGION` is a Cloud Run region such as `us-east1`.

```bash
# once, per machine: sign in and point the CLI at the right project
gcloud auth login
gcloud projects create PROJECT            # skip if you already have one
gcloud config set project PROJECT
```

Then **link a billing account to that project**, in the console under Billing, or with
`gcloud billing projects link PROJECT --billing-account=ACCOUNT_ID`. Nothing below works without it:
the `services enable` call fails on an unbilled project, and it fails by talking about the API rather
than about billing. A card on file is required even though everything here fits inside the free tier.

```bash
# once, per project
gcloud services enable run.googleapis.com secretmanager.googleapis.com artifactregistry.googleapis.com
gcloud artifacts repositories create resume --repository-format=docker --location=REGION
gcloud auth configure-docker REGION-docker.pkg.dev
printf %s "$GROQ_API_KEY" | gcloud secrets create GROQ_API_KEY --data-file=-

# every deploy
docker buildx build --platform linux/amd64 -t REGION-docker.pkg.dev/PROJECT/resume/app:vN --push .
gcloud run deploy resume-chatbot --image=REGION-docker.pkg.dev/PROJECT/resume/app:vN \
  --region=REGION --allow-unauthenticated \
  --max-instances=1 --min-instances=0 \
  --memory=1Gi --cpu=1 --timeout=300 --cpu-boost \
  --set-secrets=GROQ_API_KEY=GROQ_API_KEY:latest
```

**Repeat every flag on every deploy.** `gcloud run deploy` with `--image` replaces the revision's
configuration rather than patching it, so a flag you leave off is a flag you turned off. Dropping
`--max-instances=1` silently un-caps the budget ceiling below; dropping `--cpu-boost` silently gives
back the cold start improvement.

**`gcloud auth configure-docker` is the step people skip**, because being logged into gcloud feels
like being logged in. It is not: it writes a credential helper into your Docker config, and without
it the `docker buildx --push` below fails with a permission or unauthorized error naming the
registry, which reads like a missing IAM role rather than a missing local config. Run it once per
machine, per registry host.

`--platform linux/amd64` matters if you develop on Apple silicon: an image built without it pushes
fine and then fails at deploy.

**`--max-instances=1` is a correctness constraint, not a cost tweak:** the rate limiter and the
request budget live in process memory, so each extra instance multiplies the ceiling that is supposed
to bound your API spend, and nothing visibly changes when it does.

**`--cpu-boost` gives the container extra CPU during startup only**, so it is billed for those
seconds rather than continuously. It is the free half of the cold start story. The paid half is
`--min-instances=1`, which keeps an instance always allocated and eliminates cold start entirely, at
roughly $10 to $15 a month since a month of wall clock time is far past the free vCPU-second grant.
`--min-instances=0` above keeps this free.

Bump the tag (`:vN`) every time. Reusing one makes rollback impossible, because the old digest is
gone. Artifact Registry gives away 0.5 GB and layers are shared between tags, so a few versions cost
much less than their individual sizes suggest; check the real figure with
`gcloud artifacts repositories describe resume --location=REGION --format="value(sizeBytes)"` rather
than trusting local `docker images`.

### Hugging Face Spaces

The README front matter is still valid Space configuration (`sdk: docker`, `app_port: 7860`). Note
that as of 2026 creating a Docker Space requires a paid plan; free accounts get static Spaces only,
which cannot host this app, since there is no server process for the pipeline and the API key would
have to ship to the browser.

## Notes

- The Groq API key is only ever read from the environment. It is never in client side code.
- Public deployment guardrails are on by default: a per-IP rate limit, an hourly and daily request
  ceiling to bound API spend, an 800 character question cap, a 16 KiB request body cap rejected
  before parsing, a `max_tokens` ceiling on both model passes so no single answer can run away with
  your quota, security headers including a nonce-based CSP, and no interactive API docs. Tune them
  all in `app/config.py`.
- The container runs as an unprivileged user, not root.
- Chat latency is dominated by the Groq API and is highly variable on the free tier: five identical
  requests measured here returned in 0.60 s, 6.82 s, 9.69 s, 12.72 s and 5.57 s. Retrieval is under
  0.2 s and the page is milliseconds. A slow answer is almost never this codebase.
- Inference runs remotely on Groq, so the container loads no model weights and stays lightweight.
  The only local model is the small FastEmbed embedding model used for retrieval.
- A no-op routing seam exists in `app/chat.py` so a router (a small model that classifies simple vs
  complex questions) can be added later without refactoring.

## License

MIT. See `LICENSE`.

The MIT grant is for the code: the application in `app/`, the frontend in `static/` and
`templates/`, the generators in `tools/`, and the setup script in `scripts/`. Fork it, change it,
ship it commercially, no attribution beyond keeping the license file.

`static/vendor/` is three.js, which is somebody else's code under its own MIT license. The copyright
header is in the file and stays there. It is compatible with this one, and vendoring it changes
nothing about that.

It is not an invitation to reuse the site owner's personal material: the documents in `knowledge/`,
the contents of `profile.json`, and `static/profilepic.jpeg`. Those are somebody's biography and
photograph, and they are in the repository because the app needs them to run, not as an offer.
Replace them with your own, which is what a fork has to do anyway for the site to say anything true.
Career facts, dates, employers, and job titles are not copyrightable and this changes nothing about
them; the prose and the photograph are a different matter.

Nothing here licenses you to represent yourself as the site owner. Impersonation and misuse of a
likeness are governed by other law, and no license grants permission for either.
