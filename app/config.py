"""Application settings, loaded from environment variables.

The Groq API key must be provided via the GROQ_API_KEY environment variable
(a Secret Manager secret in production, a local .env file in development).
It is never hardcoded or shipped to the client.

Its absence is not fatal. The app boots and serves the page without it, and the
two chat endpoints return 503; see ``lifespan`` in app/main.py.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root: the Website/ directory (parent of this app/ package).
ROOT_DIR = Path(__file__).resolve().parent.parent

# Here rather than in app/profile.py because app/fingerprint.py needs it too,
# and fingerprint cannot import profile: profile imports fingerprint.
PROFILE_PATH = ROOT_DIR / "profile.json"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Secrets
    groq_api_key: str = ""

    # Models on Groq
    drafter_model: str = "openai/gpt-oss-120b"
    judge_model: str = "openai/gpt-oss-120b"
    drafter_temperature: float = 0.2
    judge_temperature: float = 0.0

    # Hard ceiling on generated length, per pass. Without this nothing bounds how
    # long an answer can run, and every question costs two 120B calls, so a single
    # rambling reply is billed twice and streams at the reader for as long as the
    # model feels like talking.
    #
    # The judge gets more room than the drafter on purpose: it receives the draft
    # and rewrites it, so a cap tighter than the drafter's would truncate a
    # perfectly good answer mid-sentence on the way out. These are limits, not
    # targets; the prompts already ask for brevity and typical answers land far
    # under both.
    drafter_max_tokens: int = 800
    judge_max_tokens: int = 1000

    # Retrieval
    knowledge_dir: Path = ROOT_DIR / "knowledge"

    # 700, not 500, and the difference is load bearing rather than a preference.
    #
    # Chunking splits by markdown heading first (see app/ingest.py), so this cap
    # applies within a section. At 500 the corpus's confidentiality section
    # does not fit: the prohibited-disclosure list and the "if asked, say it is
    # confidential" remedy land in different chunks, and retrieval can deliver
    # one without the other. At 600 and above it stays whole.
    #
    # Raising the cap costs almost nothing, which is not obvious. Sections in
    # this corpus are mostly short, so the median chunk barely moves (413 chars
    # at 500, 418 at 700); the cap only decides whether the handful of long
    # sections survive intact. Swept over 500/600/700/800/900/1000:
    #
    #   size   chunks   median   max    orphans   <200 chars   confidentiality
    #    500      56      413    636       0          4          SPLIT
    #    700      48      418    793       0          3          intact
    #   1000      45      415   1101       0          2          intact
    #
    # 700 is the smallest value that keeps it whole with headroom for the corpus
    # to grow a little. Rerun the sweep if knowledge/ changes shape.
    chunk_size: int = 700
    chunk_overlap: int = 80
    top_k: int = 5
    embedding_model: str = "BAAI/bge-small-en-v1.5"

    # Where a prebuilt index is read from, if anywhere. The image sets INDEX_DIR
    # and writes the index there at build time (see the Dockerfile), because
    # embedding the corpus is ~40 s on Cloud Run's one vCPU and it otherwise lands
    # on the first question after every cold start. Unset, as in local dev and a
    # fresh fork, the index is built in process exactly as before. See
    # app/ingest.py for why a stale one is never loaded.
    index_dir: Path | None = None

    # Request guardrails (protect the free Groq quota)
    max_question_chars: int = 800
    rate_limit_per_minute: int = 20
    # Accepted questions per client per rolling day. The global ceiling below is
    # only ~25 questions, so without this one visitor, or one script, can spend
    # the whole day's answers in a single sitting and every visitor after them
    # gets the "back tomorrow" sentence. Five is a real conversation.
    rate_limit_per_day: int = 5

    # Ceiling on the raw request body, checked from Content-Length before Pydantic
    # is handed anything. max_question_chars above only applies once the JSON has
    # already been parsed, so on its own it lets a client make the server read and
    # deserialize an arbitrarily large body just to be told the question is too
    # long. 16 KiB is ample for an 800-character question plus JSON overhead.
    max_body_bytes: int = 16 * 1024

    # Global spend ceiling. The per-client limits above are a politeness check,
    # not a budget: every accepted question costs two 120B calls, and a client
    # pool with rotating addresses walks straight past a per-client window.
    #
    # The day is set by Groq's free tier, not by money: 200,000 tokens a day and
    # never reported in a header (docs/deployment.md). At ~7,200 tokens a
    # question that is about 27 questions, shared with any eval run, so the
    # ceiling sits below it and the site says "back tomorrow" in its own words
    # before Groq starts refusing mid-answer. It was 2,000, which Groq always
    # reached first. At these numbers the hourly window cannot bind; it matters
    # again only if the day is raised, on a paid tier say.
    global_limit_per_hour: int = 300
    global_limit_per_day: int = 25

    # How many reverse proxies sit in front of the app. Behind the Cloud Run
    # edge this is 1, which means the rightmost X-Forwarded-For entry is the one
    # the proxy appended and is therefore the real client. Everything to the left
    # of it is client-supplied and must never be trusted. Set to 0 to ignore the
    # header entirely and use the socket peer (the right answer when running with
    # no proxy at all, since then XFF is pure client input).
    trusted_proxy_hops: int = 1

    # Contact form. The message is relayed to `contact_to` through Resend and
    # never stored, logged, or shown to the chatbot; see app/contact.py. Both the
    # key and the recipient come from the environment only: the recipient is the
    # owner's private inbox, which profile.json deliberately leaves blank because
    # everything in it is public. Either one missing hides the form and 503s the
    # endpoint, the same boot-anyway contract as GROQ_API_KEY.
    #
    # `onboarding@resend.dev` works without verifying a domain, but Resend only
    # delivers it to the account's own address, which is the only recipient here.
    resend_api_key: str = ""
    contact_to: str = ""
    contact_from: str = "Resume site <onboarding@resend.dev>"

    # Caps. Separate from the chat's, because the cost is different: a message
    # spends no Groq budget but lands in a real inbox, and Resend's free tier is
    # 100 emails a day. Per IP is deliberately far tighter than chat, since a
    # person writes one message, not twenty.
    max_contact_message_chars: int = 3000
    max_contact_field_chars: int = 200
    contact_limit_per_hour: int = 3
    contact_limit_per_day: int = 50

    # Serving
    static_dir: Path = ROOT_DIR / "static"


settings = Settings()
