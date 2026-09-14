"""Model access, behind one interface with two backends.

The point of the abstraction is not portability for its own sake. A bank that
must keep customer conversations on-premise still wants the cheapest model
that can do each job, and those are different jobs: routing an intent is
classification a 3B model handles well, while composing a final answer
benefits from a larger one. Keeping both behind `Backend` lets the layered
design actually spend compute where it matters, and lets a demo show the same
pipeline running fully local or against a hosted model without code changes.
"""
import os
import time
from dataclasses import dataclass


@dataclass
class Reply:
    text: str
    backend: str
    model: str
    seconds: float
    tokens: int


class Backend:
    name = "base"

    def complete(self, system, user, max_tokens=400, temperature=0.0):
        raise NotImplementedError


class LocalMLX(Backend):
    """On-device inference via Apple MLX. Nothing leaves the machine."""

    name = "local"

    def __init__(self, model_id="mlx-community/Qwen2.5-3B-Instruct-4bit"):
        self.model_id = model_id
        self._model = None
        self._tokenizer = None

    def _ensure_loaded(self):
        # Loading costs over a minute, so it happens once per process and only
        # if a local call is actually made.
        if self._model is None:
            from mlx_lm import load

            self._model, self._tokenizer = load(self.model_id)

    def complete(self, system, user, max_tokens=400, temperature=0.0):
        from mlx_lm import generate
        from mlx_lm.sample_utils import make_sampler

        self._ensure_loaded()
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": user}]
        prompt = self._tokenizer.apply_chat_template(messages, add_generation_prompt=True)
        started = time.time()
        text = generate(self._model, self._tokenizer, prompt=prompt,
                        max_tokens=max_tokens, verbose=False,
                        sampler=make_sampler(temp=temperature))
        return Reply(text=text.strip(), backend=self.name, model=self.model_id,
                     seconds=round(time.time() - started, 2),
                     tokens=len(self._tokenizer.encode(text)))


class CloudAnthropic(Backend):
    """Hosted inference, for demonstrating the quality ceiling of the same
    pipeline. Unavailable without a key, by design: the local path must never
    silently depend on it."""

    name = "cloud"

    def __init__(self, model_id="claude-sonnet-5"):
        self.model_id = model_id
        self._client = None

    @property
    def available(self):
        return bool(os.environ.get("ANTHROPIC_API_KEY"))

    def complete(self, system, user, max_tokens=400, temperature=0.0):
        import anthropic

        if self._client is None:
            self._client = anthropic.Anthropic()
        started = time.time()
        resp = self._client.messages.create(
            model=self.model_id, max_tokens=max_tokens, temperature=temperature,
            system=system, messages=[{"role": "user", "content": user}])
        text = "".join(b.text for b in resp.content if b.type == "text")
        return Reply(text=text.strip(), backend=self.name, model=self.model_id,
                     seconds=round(time.time() - started, 2),
                     tokens=resp.usage.output_tokens)


def get_backend(name=None):
    """Resolve a backend by name, defaulting to local.

    Local is the default because it is the deployment the design targets;
    the cloud path is opt-in and fails loudly when its key is missing rather
    than falling back, so an evaluation run can never quietly change what it
    was measuring.
    """
    name = name or os.environ.get("ASSISTANT_BACKEND", "local")
    if name == "local":
        return LocalMLX()
    if name == "cloud":
        backend = CloudAnthropic()
        if not backend.available:
            raise RuntimeError("cloud backend 需要 ANTHROPIC_API_KEY")
        return backend
    raise ValueError(f"unknown backend: {name}")
