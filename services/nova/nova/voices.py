"""Nova's voice shortlist: the same approved lines in each candidate voice, as WAV files to
compare against the brand book: warm, mid-low, unhurried (about 150 words a minute), clear
and playful, and never a soundalike of a real person.

    uv run python -m nova.voices

Costs LiveKit credit: about 1,100 characters in all, roughly $0.05. The files go to
services/nova/voice-samples/, which is never committed. For each voice it prints how long
the first audio took and how fast the voice speaks.
"""

import asyncio
import statistics
import time
import wave
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import aiohttp
from livekit.agents import inference

from nova import script
from nova.config import DEFAULT_VOICE, Settings, load_settings
from nova.fixtures import MAYA, REHEARSAL_PAIR
from nova.measure import ms
from nova.rehearsal import TTS_PER_CHARACTER, price

SAMPLES = Path(__file__).parents[1] / "voice-samples"
GAP_SECONDS = 0.7


@dataclass(frozen=True)
class Candidate:
    model: str
    voice: str
    label: str

    @property
    def setting(self) -> str:
        """The NOVA_VOICE value for this voice."""
        return f"{self.model}:{self.voice}"


# From LiveKit's suggested voices, matched to the brand book's description.
CANDIDATES = (
    Candidate(
        *DEFAULT_VOICE.split(":", 1),  # the default voice (nova/config.py), written once
        "Cartesia Sonic 3, Robyn: neutral, mature (Australian English)",
    ),
    Candidate(
        "inworld/inworld-tts-1.5-max",
        "Ashley",
        "Inworld 1.5 Max, Ashley: warm, natural (American English)",
    ),
    Candidate(
        "deepgram/aura-2",
        "odysseus",
        "Deepgram Aura-2, Odysseus: calm, professional (American English)",
    ),
)


def sample_lines() -> list[str]:
    """Approved lines only, so every voice reads exactly the same words."""
    return [
        script.doors_fallback(REHEARSAL_PAIR),
        script.first_question_fallback(MAYA),
        script.NEED_A_MOMENT,
        script.time_line(REHEARSAL_PAIR),
        script.RESULT_NOT_MUTUAL,
        script.INVITATION,
    ]


def words_per_minute(lines: Iterable[str], *, seconds: float) -> float:
    words = sum(len(line.split()) for line in lines)
    return words / seconds * 60 if seconds else 0.0


def write_wav(path: Path, chunks: Sequence[bytes], *, sample_rate: int) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        for chunk in chunks:
            wav.writeframes(chunk)


@dataclass(frozen=True)
class Sample:
    candidate: Candidate
    first_audio_ms: list[int]
    speech_seconds: float
    characters: int
    path: Path | None
    error: str | None = None


async def record(
    candidate: Candidate, lines: list[str], *, settings: Settings, http: aiohttp.ClientSession
) -> Sample:
    tts = inference.TTS(
        candidate.model,
        voice=candidate.voice,
        api_key=settings.livekit_api_key,
        api_secret=settings.livekit_api_secret,
        http_session=http,
    )
    chunks: list[bytes] = []
    first_audio: list[int] = []
    speech = 0.0
    sample_rate = 24000
    try:
        for text in lines:
            asked = time.monotonic()
            first: float | None = None
            async with tts.synthesize(text) as stream:
                async for audio in stream:
                    first = first or time.monotonic()
                    frame = audio.frame
                    sample_rate = frame.sample_rate
                    speech += frame.samples_per_channel / frame.sample_rate
                    chunks.append(bytes(frame.data))
            first_audio.append(ms((first or asked) - asked))
            chunks.append(b"\x00\x00" * int(sample_rate * GAP_SECONDS))
    except Exception as error:  # a voice that fails is reported, not fatal
        return Sample(candidate, first_audio, speech, 0, None, type(error).__name__)
    finally:
        await tts.aclose()
    SAMPLES.mkdir(exist_ok=True)
    path = SAMPLES / f"{candidate.model.replace('/', '-')}-{candidate.voice[:12]}.wav"
    write_wav(path, chunks, sample_rate=sample_rate)
    return Sample(candidate, first_audio, speech, sum(len(line) for line in lines), path)


def render(samples: list[Sample], lines: list[str]) -> str:
    rows = ["Nova voice shortlist (brand book: about 150 words a minute)", ""]
    for s in samples:
        rows.append(f"{s.candidate.label}")
        rows.append(f"  NOVA_VOICE={s.candidate.setting}")
        if s.error:
            rows.append(f"  failed: {s.error}")
            continue
        cost = s.characters * price(TTS_PER_CHARACTER, s.candidate.model)
        rows.append(
            f"  first audio: median {statistics.median(s.first_audio_ms)} ms, worst "
            f"{max(s.first_audio_ms)} ms; {words_per_minute(lines, seconds=s.speech_seconds):.0f}"
            f" words a minute; {s.characters} characters (about ${cost:.3f})"
        )
        rows.append(f"  {s.path}")
    return "\n".join(rows)


async def main_async() -> str:
    settings = load_settings()
    lines = sample_lines()
    async with aiohttp.ClientSession() as http:
        samples = [
            await record(candidate, lines, settings=settings, http=http) for candidate in CANDIDATES
        ]
    return render(samples, lines)


def main() -> None:
    print(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
