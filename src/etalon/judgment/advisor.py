"""Ask an advisor a question, and refuse an answer that does not answer it.

Three properties of this module matter more than the transport it happens to use.

**The answer is parsed strictly and never coerced.** A question asked here names the exact
shape of the reply, and a reply of the wrong shape is refused rather than salvaged. This is
not fastidiousness: the measured failure mode of these systems is that structured-looking
output carries an impression of rigour its content has not earned, and the natural response to
a half-formatted answer -- reach in and pull out what looks like the number -- is the same
operation that turns a hallucinated value into a recorded one. A refused answer costs one
more call. A coerced answer costs a campaign.

**The question is short and asks for a decision, not for reasoning.** Hallucinations in the
drug-discovery benchmarks appeared predominantly under chain-of-thought prompting, as spurious
intermediate-reasoning sentences and as numerical attributes that disagreed with ground truth.
So the prompts built here ask for a choice plus one sentence, and the one sentence is recorded
and acted on by nothing.

**Retries are counted, never hidden.** A second attempt is a different answer to the same
question, and a record that shows one answer where three were asked for has lost the fact that
the first two were unusable -- which is exactly the signal a reader needs.

The transport is deliberately a small protocol. ``ClaudeCli`` shells out to the ``claude``
binary, which needs no API key because it uses the operator's existing login; ``Scripted``
replays fixed answers so that every test in this repository runs with no network and no
credentials. Nothing else in ETALON knows which was used, except the ledger, which records it.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from etalon.judgment.proposal import Act, Advisor, AdvisorKind, Proposal, digest

#: Pulled out of a fenced block if the model wraps its answer in one. Tolerated because it is
#: a formatting habit rather than a content change -- the object inside is still required to
#: be exactly what was asked for.
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class AdvisorError(RuntimeError):
    """Raised when an advisor could not be reached, or answered something else."""


class Transport(Protocol):
    """How a question reaches an advisor. One method, so a test can be a dictionary."""

    #: What the ledger records as the transport.
    name: str

    def ask(self, prompt: str) -> str: ...


@dataclass(frozen=True, slots=True)
class ClaudeCli:
    """Reach Claude through the ``claude`` binary already on the operator's machine.

    Chosen over an HTTP call on purpose. It needs no API key -- it uses the login the operator
    already has -- so running a campaign with an advisor attached requires no new secret in
    the environment, and a secret that does not exist cannot end up in a ledger line.

    ``-p`` is one-shot and non-interactive: one question, one answer, no session that could
    carry reasoning from a previous round into this one. Long-horizon memory degradation is a
    documented failure of these systems, and the campaign's own memory is the append-only
    ledger, which is auditable in a way a model's context is not.
    """

    model: str = "sonnet"
    binary: str = "claude"
    timeout: int = 300
    name: str = "claude-cli"

    def ask(self, prompt: str) -> str:
        found = shutil.which(self.binary)
        if found is None:
            raise AdvisorError(
                f"no {self.binary!r} on PATH. This transport uses the operator's existing "
                "login rather than an API key, so it needs the CLI itself to be installed."
            )
        try:
            completed = subprocess.run(
                [found, "-p", prompt, "--model", self.model],
                capture_output=True,
                text=True,
                check=False,
                timeout=self.timeout,
                # A clean cwd: the CLI reads project configuration from where it is run, and a
                # campaign's answers must not depend on which directory happened to be current.
                cwd=str(Path.home()),
            )
        except subprocess.TimeoutExpired as expired:
            raise AdvisorError(
                f"{self.binary} did not answer within {self.timeout}s"
            ) from expired
        if completed.returncode != 0:
            raise AdvisorError(
                f"{self.binary} exited {completed.returncode}: "
                f"{completed.stderr.strip()[-400:] or 'no stderr'}"
            )
        return completed.stdout


@dataclass(frozen=True, slots=True)
class Scripted:
    """Fixed answers, so the suite runs with no network and no credentials.

    Keyed by a substring of the prompt rather than by position, so a test says which question
    it is answering. A prompt matching no key is an error rather than a default: a scripted
    advisor that silently answers an unexpected question tests the harness against a fiction.
    """

    replies: Mapping[str, str]
    name: str = "scripted"
    #: Mutated by ``ask`` so a test can assert how many times it was consulted.
    asked: list[str] = field(default_factory=list)

    def ask(self, prompt: str) -> str:
        self.asked.append(prompt)
        for key, reply in self.replies.items():
            if key in prompt:
                return reply
        raise AdvisorError(
            f"the scripted advisor has no answer for this question. It holds "
            f"{sorted(self.replies)} and was asked something else, which would otherwise be "
            "answered by a default and tested against a fiction."
        )


def _parse(text: str, required: Sequence[str]) -> dict[str, Any]:
    """The one object the question asked for, or an error naming what came back instead."""

    candidate = text.strip()
    fenced = _FENCE.search(candidate)
    if fenced:
        candidate = fenced.group(1).strip()
    # A model that prefaces its JSON with a sentence: take the outermost braces. Tolerated
    # for the same reason as the fence -- it is a wrapper, not a different answer.
    if not candidate.startswith("{"):
        start, end = candidate.find("{"), candidate.rfind("}")
        if start >= 0 and end > start:
            candidate = candidate[start : end + 1]
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as error:
        raise AdvisorError(
            f"the answer is not the object the question asked for ({error}). Refused rather "
            "than searched for a number: reaching into a malformed answer for the part that "
            f"looks right is how a hallucinated value becomes a recorded one. Got: "
            f"{text.strip()[:240]!r}"
        ) from error
    if not isinstance(parsed, dict):
        raise AdvisorError(f"expected an object, got {type(parsed).__name__}")
    missing = [key for key in required if key not in parsed]
    if missing:
        raise AdvisorError(
            f"the answer is missing {missing}, so it does not answer the question that was "
            f"asked. It carries {sorted(parsed)}."
        )
    return parsed


@dataclass(frozen=True, slots=True)
class Advisory:
    """One advisor, reachable, recording everything it was asked and everything it said."""

    transport: Transport
    identifier: str
    kind: AdvisorKind = AdvisorKind.LANGUAGE_MODEL
    #: How many differently-shaped answers to tolerate before giving up. Counted into the
    #: record, because a question that took three attempts is a different kind of evidence
    #: from one that took one.
    attempts: int = 2

    def ask(
        self,
        act: Act,
        question: str,
        *,
        required: Sequence[str],
        context: str = "",
    ) -> Proposal:
        """Put one question, and return a proposal or raise.

        The prompt is assembled here rather than by the caller so that every question carries
        the same two instructions -- answer with one JSON object, and keep the reason to one
        sentence -- and so that its digest is taken over the text that was actually sent.
        """

        prompt = _PROMPT.format(
            act=act.value,
            question=question.strip(),
            context=context.strip() or "(none)",
            keys=", ".join(required),
        )
        problems: list[str] = []
        for attempt in range(1, self.attempts + 1):
            raw = self.transport.ask(prompt)
            try:
                parsed = _parse(raw, required)
            except AdvisorError as error:
                problems.append(f"attempt {attempt}: {error}")
                continue
            rationale = str(parsed.pop("reason", "")).strip()
            return Proposal(
                act=act,
                advisor=Advisor(
                    kind=self.kind,
                    identifier=self.identifier,
                    transport=getattr(self.transport, "name", "unknown"),
                    prompt_sha256=digest(prompt),
                    response_sha256=digest(raw),
                ),
                payload={
                    **{key: parsed[key] for key in required if key in parsed},
                    "_attempts": attempt,
                    **({"_earlier_attempts": problems} if problems else {}),
                },
                rationale=rationale,
            )
        raise AdvisorError(
            f"{self.identifier} did not answer the {act.value} question in the shape it was "
            f"asked for, in {self.attempts} attempts:\n" + "\n".join(problems)
        )


#: Deliberately short, and deliberately discouraging extended reasoning: the measured
#: hallucination rate in drug-discovery benchmarks was highest under chain-of-thought, and the
#: reason field here is read by people and acted on by nothing.
_PROMPT = """\
You are advising a computational drug-discovery campaign. This is a {act} question.

{question}

Context:
{context}

Answer with exactly one JSON object and no other text. It must contain these keys: {keys}.
Add a "reason" key holding one sentence. Do not explain your reasoning at length; if you are
not confident, say so in the reason rather than choosing differently.
"""


__all__ = ["Advisory", "AdvisorError", "ClaudeCli", "Scripted", "Transport"]
