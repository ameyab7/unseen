"""Who may receive what Nova sends a language model: one policy, asked where each prompt is
written and asked again as each request leaves (decision 0010, docs/privacy/).

Every request that may carry a dater's information goes through Guarded, the policy's
wrapper on a model: set pieces before the date, live lines during it, the meaning check,
and the distress and conduct check. Where a request may go depends on its endpoint:

- Approved local: a model on this machine, at a loopback address (localhost, 127.0.0.0/8,
  ::1), or a host the operator lists in NOVA_APPROVED_LOCAL_HOSTS because it's theirs and
  nobody else receives what it's sent. Nothing else is local: not host.docker.internal
  (which Docker resolves to whatever machine runs it), not a model because its provider is
  Ollama, and not a model whose endpoint isn't known.
- Anything else is a provider: Anthropic always, Ollama anywhere else.

Two kinds of data come from what daters say to each other, and they're kept apart:

- Dialogue memory, this date's recent turns (decision 0008, DATE_CONTEXT): for lines that
  follow the date. It goes only to an approved-local endpoint, or to a provider once
  NOVA_DIALOGUE_TO_PROVIDER says the privacy notice covers it.
- Conversation-derived safety context (SAFETY_CONTEXT): each utterance the safety check
  reads, with both daters' words from the 15 seconds before it. It goes to the classifier,
  wherever that is, whatever NOVA_DIALOGUE_TO_PROVIDER says: holding it back would change
  the frozen check (decision 0005). A hosted classifier needs production approval
  (decision 0010).

For dialogue memory: Where they may
not go, the prompt is written without them (the line writer asks `allows`). A request that
carries them anyway is never sent: Guarded raises PrivacyBlocked, which the caller handles
like any failed request (a fallback line, a line not kept, a moderator alert).

What else the configured models need goes to them wherever they are: the safety context,
the cards, a dater's words to Nova, audience questions and Nova's own lines. Choosing a
provider in NOVA_LLM or NOVA_CLASSIFIER_LLM is what sends those, so the privacy notice
must name it first.
"""

import ipaddress
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Self
from urllib.parse import urlsplit

from nova.config import ConfigError, Settings


class Data(StrEnum):
    """What a flow carries (nova/data_flows.py)."""

    RAW_AUDIO = "raw audio"
    # What a dater said that a line answers, or that a line is judged against: their words
    # to Nova.
    TRANSCRIPT = "transcripts"
    # Dialogue memory (decision 0008): up to 8 turns, both daters. The only kind the policy
    # holds back by recipient.
    DATE_CONTEXT = "this date's recent turns"
    # An utterance the safety check reads, with both daters' words from the 15 seconds before
    # it: to the classifier wherever it is (decision 0010). Named for the inventory and the
    # notice; nothing at run time asks about it.
    SAFETY_CONTEXT = "conversation-derived safety context"
    NOVA_TEXT = "Nova's lines"  # its lines, candidates and tonight's memory
    PROFILE = "card and profile"  # first names, ages, pronouns, card answers
    AUDIENCE = "audience questions"
    METADATA = "session metadata"  # room, job, agent and session IDs; identities


@dataclass(frozen=True)
class Recipient:
    """Who receives a model's requests. local: an approved-local endpoint."""

    name: str
    local: bool


class PrivacyBlocked(Exception):
    """A request carried what its recipient may not receive, so it was never sent. The
    message names the kind of data, never the data."""


# The keys under which a prompt's data holds this date's turns: a reply's or a silence's
# (nova/prompts.py) and the meaning check's (nova/meaning.py). At the data block's top level,
# one space in (json.dumps with indent=1), a key can only be the prompt's own: a dater's
# words are JSON strings, whose quotes and line breaks are escaped. "{}" is none.
_THIS_DATE = re.compile(r'^ "(?:this_date|said_on_this_date)": (?!\{\})', re.MULTILINE)

# The SDK loggers that write each request they send, prompt and all, at DEBUG ("Request
# options"): a dater's words with it.
REQUEST_LOGGERS = ("anthropic._base_client", "openai._base_client")


@dataclass(frozen=True)
class PrivacyPolicy:
    """dialogue_to_provider: NOVA_DIALOGUE_TO_PROVIDER. approved_hosts:
    NOVA_APPROVED_LOCAL_HOSTS. synthetic: a run on made-up daters only (for_synthetic_data)."""

    dialogue_to_provider: bool = False
    approved_hosts: frozenset[str] = field(default_factory=frozenset)
    synthetic: bool = False

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        return cls(settings.dialogue_to_provider, settings.approved_local_hosts)

    @classmethod
    def for_synthetic_data(cls) -> Self:
        """Anything may go anywhere: only for the evaluation and the benchmark, whose daters
        are made up. Never where real people are heard (a test holds the agent to it)."""
        return cls(synthetic=True)

    def recipient(self, model: Any) -> Recipient:
        """Who a model's requests go to: from its provider and its endpoint, if it has one."""
        while isinstance(model, Guarded):
            model = model.inner
        provider = model.name.partition("/")[0]
        return self.recipient_of(provider, getattr(model, "endpoint", None))

    def recipient_of(self, provider: str, endpoint: str | None) -> Recipient:
        if provider == "anthropic":
            return Recipient("Anthropic", local=False)
        label = provider.capitalize()
        host = _host(endpoint)
        if host is None:
            return Recipient(f"{label} (endpoint unknown)", local=False)
        return Recipient(f"{label} at {host}", local=_loopback(host) or host in self.approved_hosts)

    def allows(self, model: Any, data: Data) -> bool:
        """Whether this model may receive this kind of data."""
        if data is not Data.DATE_CONTEXT:
            return True
        return self._context_may_go(model)

    def check(self, model: Any, *texts: str) -> None:
        """Raises PrivacyBlocked, before anything is sent, if a request to this model carries
        this date's turns and it may not receive them."""
        if not self._context_may_go(model) and any(_THIS_DATE.search(t) for t in texts):
            raise PrivacyBlocked(Data.DATE_CONTEXT.value)

    def guard(self, model: Any) -> Guarded:
        """The model with every request checked against this policy before it's sent."""
        if isinstance(model, Guarded) and model.policy == self:
            return model
        return Guarded(model, self)

    def _context_may_go(self, model: Any) -> bool:
        return self.synthetic or self.dialogue_to_provider or self.recipient(model).local


class Guarded:
    """A model whose requests the policy checks before each is sent (PrivacyPolicy.check)."""

    def __init__(self, model: Any, policy: PrivacyPolicy) -> None:
        self.inner, self.policy = model, policy
        self.name: str = model.name
        self.recipient = policy.recipient(model)
        if hasattr(model, "sample"):  # the benchmark's actors: checked like any request
            self.sample = self._sample

    async def write(self, *, system: str, prompt: str) -> str:
        self.policy.check(self.inner, system, prompt)
        return await self.inner.write(system=system, prompt=prompt)

    async def label(
        self, *, system: str, prompt: str, schema: Mapping[str, object] | None = None
    ) -> str:
        self.policy.check(self.inner, system, prompt)
        options = {} if schema is None else {"schema": schema}
        return await self.inner.label(system=system, prompt=prompt, **options)

    async def _sample(self, *, system: str, prompt: str, temperature: float, seed: int) -> str:
        self.policy.check(self.inner, system, prompt)
        return await self.inner.sample(
            system=system, prompt=prompt, temperature=temperature, seed=seed
        )


def logs_requests() -> list[str]:
    """The SDK loggers that would write each request's prompt: any enabled for DEBUG."""
    return [name for name in REQUEST_LOGGERS if logging.getLogger(name).isEnabledFor(logging.DEBUG)]


def check_for_real_people(settings: Settings) -> None:
    """Where Nova hears real people (the LiveKit agent): no diagnostics meant for synthetic
    runs, and no SDK logger that writes prompts, however it was turned on. Raises
    ConfigError naming what to change, before anyone is heard."""
    if settings.synthetic_diagnostics:
        raise ConfigError(
            "NOVA_SYNTHETIC_DIAGNOSTICS is for runs on made-up daters (the evaluation, the "
            "benchmark), never the agent: it hears real people"
        )
    logged = logs_requests()
    if logged:
        raise ConfigError(
            f"{', '.join(logged)} would log every prompt Nova sends, daters' words included: "
            "set it to INFO or above"
        )


def _host(endpoint: str | None) -> str | None:
    if not endpoint:
        return None
    try:
        host = urlsplit(endpoint).hostname
    except ValueError:
        return None
    return host.lower() if host else None


def _loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
