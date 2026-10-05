"""The answer pipeline: retrieve, draft, then judge.

The public entry points are ``ChatEngine.answer`` (blocking, returns the whole
answer) and ``ChatEngine.stream_answer`` (yields the answer token by token). Both
run the same flow:

    question -> route -> retrieve -> draft (GPT-OSS 120B) -> judge (second pass) -> final answer

``route`` is a deliberate no-op seam today. Later a small, fast model (for example
GPT-OSS 20B) can classify simple vs complex questions here and pick a model, without
refactoring the rest of the pipeline.

Streaming deliberately covers the JUDGE pass only. The draft is produced in full
first and never reaches the browser. Streaming the draft would be faster to first
token, but it would put unverified text on screen and then rewrite it, which is
exactly the guarantee this site is built to make. The wait for the draft is what
the thinking indicator covers.
"""

from collections.abc import Iterator
from dataclasses import dataclass

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq

from . import prompts, retriever
from .config import settings
from .ingest import build_vectorstore
from .profile import load_profile


@dataclass
class Answer:
    text: str
    sources: list[str]


# What the pipeline says when it has nothing to say.
#
# A stream that yields no token is not a harmless non-answer. The client treated
# it as a failed stream and retried on the blocking endpoint, so a question that
# produced nothing ran both passes twice and still rendered an empty bubble.
# Against a free tier that serves roughly thirty questions a day, that is a real
# answer some other visitor does not get. So the pipeline always says something,
# and it says it here rather than in a prompt, because a prompt rule is a request
# to a model and this is a guarantee.
_NOTHING_TO_SAY = "I'm sorry, I can't help with that. Feel free to ask me something else about {name}."


class ChatEngine:
    def __init__(self, *, use_judge: bool = True) -> None:
        # A backstop, not the user-facing check. `_check_request` in app/main.py
        # rejects a keyless chat request with a 503 long before this runs, so in
        # the served app this is unreachable. It stays because ChatEngine is also
        # constructed directly, by the test suite and by anything that imports it
        # outside the app, and there the useful failure is loud and immediate
        # rather than a confusing 401 from Groq several seconds later.
        if not settings.groq_api_key:
            raise RuntimeError(
                "GROQ_API_KEY is not set. Set it in the environment (a Secret Manager "
                "secret in production, a .env file locally) before building a ChatEngine."
            )

        self.use_judge = use_judge
        self.owner_name = load_profile()["known_as"]
        self.vectorstore = build_vectorstore()
        self.always_on = retriever.build_always_on_summary()

        # max_tokens on both passes is a budget control, not a style one. Every
        # question costs two 120B calls, so an unbounded answer is billed twice.
        # The judge's ceiling is the higher of the two: it rewrites the draft it
        # is given, so capping it below the drafter would cut a good answer off
        # mid-sentence on the way out. See config.py for the reasoning.
        self.drafter = ChatGroq(
            model=settings.drafter_model,
            temperature=settings.drafter_temperature,
            max_tokens=settings.drafter_max_tokens,
            api_key=settings.groq_api_key,
        )
        self.judge = ChatGroq(
            model=settings.judge_model,
            temperature=settings.judge_temperature,
            max_tokens=settings.judge_max_tokens,
            api_key=settings.groq_api_key,
        )

    def route(self, question: str) -> str:
        """No-op routing seam. Returns the drafter model name to use for this question.

        Kept as an explicit hook so a router pattern can drop in later.
        """
        return settings.drafter_model

    def _draft(self, question: str, context: str) -> str:
        messages = [
            SystemMessage(
                content=prompts.DRAFTER_SYSTEM.format(name=self.owner_name, context=context)
            ),
            HumanMessage(content=question),
        ]
        return self.drafter.invoke(messages).content.strip()

    def _judge(self, question: str, context: str, draft: str) -> str:
        messages = [
            SystemMessage(
                content=prompts.JUDGE_SYSTEM.format(name=self.owner_name, context=context)
            ),
            HumanMessage(
                content=prompts.JUDGE_USER.format(question=question, draft=draft)
            ),
        ]
        return self.judge.invoke(messages).content.strip()

    def _judge_stream(self, question: str, context: str, draft: str) -> Iterator[str]:
        messages = [
            SystemMessage(
                content=prompts.JUDGE_SYSTEM.format(name=self.owner_name, context=context)
            ),
            HumanMessage(
                content=prompts.JUDGE_USER.format(question=question, draft=draft)
            ),
        ]
        for chunk in self.judge.stream(messages):
            text = chunk.content
            if text:
                yield text

    def _nothing_to_say(self) -> str:
        return _NOTHING_TO_SAY.format(name=self.owner_name)

    def answer(self, question: str) -> Answer:
        self.route(question)  # seam; result unused today

        retrieved = retriever.retrieve(self.vectorstore, question)
        context = retriever.format_context(retrieved, self.always_on)
        sources = retriever.sources_of(retrieved)

        draft = self._draft(question, context)

        # An empty draft carries no claims, so there is nothing for a checking
        # pass to check, and whatever the judge returns is the judge answering
        # the question itself from context. That is exactly how a raw corpus
        # section reached the output once already. Not calling it is the only
        # version of this guarantee that cannot be argued with, and it saves a
        # 120B request on a budget that has few to spare.
        if not draft.strip():
            return Answer(text=self._nothing_to_say(), sources=sources)

        final = self._judge(question, context, draft) if self.use_judge else draft

        # Falling back to the draft here would show text the judge never passed.
        return Answer(text=final.strip() or self._nothing_to_say(), sources=sources)

    def stream_answer(self, question: str) -> Iterator[tuple[str, object]]:
        """Run the pipeline and yield ``(event, payload)`` pairs.

        Events, in order:
            ("sources", list[str])  once, as soon as retrieval is done
            ("token",   str)        repeatedly, as the final answer is written
            ("done",    None)       once, after the last token

        Sources are sent up front rather than at the end so the client has them
        before the first token lands, which keeps the rendering path in the
        browser a simple append.
        """
        self.route(question)  # seam; result unused today

        retrieved = retriever.retrieve(self.vectorstore, question)
        context = retriever.format_context(retrieved, self.always_on)
        yield "sources", retriever.sources_of(retrieved)

        draft = self._draft(question, context)

        # Same short circuit as the blocking path, and for the same reason: on
        # this path the judge's own answer would reach the browser token by
        # token, already on screen before anything could retract it.
        if not draft.strip():
            yield "token", self._nothing_to_say()
            yield "done", None
            return

        if not self.use_judge:
            yield "token", draft
            yield "done", None
            return

        # Leading whitespace from the model would otherwise render as a gap
        # before the first word, since there is no final .strip() when
        # streaming. Suppress it until real text arrives.
        started = False
        for piece in self._judge_stream(question, context, draft):
            if not started:
                piece = piece.lstrip()
                if not piece:
                    continue
                started = True
            yield "token", piece

        # The suppressor above skips whitespace while waiting for real text, so
        # a judge that streams only whitespace exits this loop having yielded
        # nothing at all, indistinguishable to the browser from a judge that
        # said nothing. Either way the stream still has to say so.
        if not started:
            yield "token", self._nothing_to_say()

        yield "done", None
