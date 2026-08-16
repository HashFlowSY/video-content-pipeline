"""Synthetic media fixture generator for the Phase 10 engineering twin.

Phase 10 verifies the pipeline against *synthetic* media that mirrors, one for
one, the mandatory real-video branches Phase 11 will exercise against real
media. This module turns versioned :class:`FixtureRecipe` records into tiny media
files (seconds long, ~160x120) using the host ffmpeg identity-pinned in
``config/tools.json``. The files are generated once per test session into a
caller-supplied cache directory (pytest's session-scoped ``tmp_path_factory``)
and are **never committed** — the repository stays free of media binaries.

Identity before use, as a test error not a skip
    :func:`resolve_fixture_toolchain` compares the current ffmpeg/ffprobe binary
    identity (SHA-256 + ``-version`` line) against the pinned evidence in
    ``config/tools.json`` and raises :class:`FixtureToolchainError` on absence or
    mismatch. Because :func:`generate_fixture` only accepts a
    :class:`FixtureToolchain` — which can be obtained *only* from that verifier —
    the type itself is the proof that identity was checked before first use. A
    caller that skips instead of erroring would be violating the contract; the
    verifier never returns ``None`` and never calls ``pytest.skip``.

Five branches
    1. ``subtitle-first`` — video + audio + a muxed subtitle track.
    2. ``full-asr`` — video + audio, *no* subtitle track (transcription needed).
    3. ``anomalous-subtitles`` — a crafted subtitle file (rolling repeats and
       drifting/overlapping timestamps) muxed onto video + audio.
    4. ``multi-part`` — a collection of three separate video + audio files.
    5. ``visual-text`` — text-bearing frames. The pinned host ffmpeg is built
       *without* libfreetype/libass, so neither ``drawtext`` nor the
       ``subtitles`` burn-in filter is available; instead the ``testsrc`` source
       is used, which renders a burned-in frame counter (digit glyphs) with its
       own built-in vector font — genuine rendered text in every frame, produced
       entirely by the pinned toolchain.

The module imports no pytest: like the fault-injection kit it is plain
importable support code, and the session-scoped caching lives in the test that
consumes it. It reuses :func:`video_content_pipeline.external_tools`'s vetted
identity capture so the fixture toolchain check and production tool-pinning agree
byte for byte.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from video_content_pipeline.external_tools import (
    ExternalToolError,
    identify_external_tool,
)

#: Bump when a recipe's bytes-affecting definition changes; the cache is keyed on
#: it so a stale build from an earlier version never masquerades as current.
#: v2: audio moved to a 32 kHz sample rate so decoded packet coverage is gap-free
#: (see :data:`_AUDIO_SAMPLE_RATE`).
RECIPES_VERSION = 2

#: Placeholders substituted into a recipe's argv at generation time. Keeping the
#: argv otherwise fully literal means no shell and no path interpolation.
_SUBTITLE_TOKEN = "@SUBTITLE@"
_OUTPUT_TOKEN = "@OUTPUT@"

#: Marker file written once a branch's parts are all present, so a second call in
#: the same session regenerates nothing.
_COMPLETE_MARKER = ".complete"


class FixtureToolchainError(RuntimeError):
    """The pinned ffmpeg/ffprobe toolchain is absent or does not match evidence.

    Raised — never swallowed into a skip — so a missing or drifted binary fails
    the affected tests loudly, exactly as the Phase 10 verification boundary
    requires.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class FixtureGenerationError(RuntimeError):
    """The pinned ffmpeg refused to produce a fixture part."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class FixtureToolchain:
    """Verified identity of the ffmpeg/ffprobe pair used to build fixtures.

    Only :func:`resolve_fixture_toolchain` constructs this, and only after the
    identity check passes, so holding one is proof the check ran.
    """

    ffmpeg: Path
    ffprobe: Path
    ffmpeg_version: str
    ffprobe_version: str


@dataclass(frozen=True)
class MediaBuild:
    """One media file: the ffmpeg argv that builds it and its expected streams.

    ``argv`` is the argument list *after* the binary; ``_OUTPUT_TOKEN`` marks the
    output path and ``_SUBTITLE_TOKEN`` (when present) the crafted subtitle input.
    ``expected_streams`` is the sorted ``codec_type`` tuple ffprobe must report.
    """

    output: str
    argv: tuple[str, ...]
    expected_streams: tuple[str, ...]


@dataclass(frozen=True)
class FixtureRecipe:
    """A versioned recipe for one Phase 11 branch's synthetic twin."""

    fixture_id: str
    description: str
    subtitle_source: str | None
    builds: tuple[MediaBuild, ...]

    @property
    def outputs(self) -> tuple[str, ...]:
        return tuple(build.output for build in self.builds)


@dataclass(frozen=True)
class GeneratedFixture:
    """The concrete media files produced for a recipe in one cache directory.

    ``regenerated`` is ``True`` when this call actually ran ffmpeg and ``False``
    when every part was already present from an earlier call this session — the
    observable proof that generation is session-cached.
    """

    recipe: FixtureRecipe
    parts: tuple[Path, ...]
    regenerated: bool


# -- crafted subtitle payloads ----------------------------------------------

#: A well-formed subtitle track for the subtitle-first branch.
_ORDINARY_SUBTITLES = (
    "1\n"
    "00:00:00,000 --> 00:00:01,000\n"
    "first cue\n\n"
    "2\n"
    "00:00:01,000 --> 00:00:02,000\n"
    "second cue\n\n"
    "3\n"
    "00:00:02,000 --> 00:00:03,000\n"
    "third cue\n"
)

#: Rolling repeats (a growing prefix, then an exact duplicate) and drifting,
#: overlapping timestamps — the anomalous subtitle shape Phase 11's anomalous
#: real-video branch must survive.
_ANOMALOUS_SUBTITLES = (
    "1\n"
    "00:00:00,000 --> 00:00:01,050\n"
    "we\n\n"
    "2\n"
    "00:00:00,900 --> 00:00:02,100\n"
    "we need\n\n"
    "3\n"
    "00:00:01,950 --> 00:00:03,200\n"
    "we need proof\n\n"
    "4\n"
    "00:00:03,000 --> 00:00:04,000\n"
    "proof\n\n"
    "5\n"
    "00:00:03,000 --> 00:00:04,000\n"
    "proof\n"
)


#: Audio is FLAC at 32 kHz for every fixture with an audio stream. Both choices
#: keep the decoded packet coverage a single gap-free interval that starts at
#: zero — the shape the analysis-audio derivation requires. At 32 kHz a whole
#: FLAC frame lands on an exact millisecond, so it tiles Matroska's millisecond
#: timebase without the rounding that scatters ~1 ms coverage gaps at 48 kHz; and
#: FLAC, unlike AAC, adds no encoder priming, so coverage starts at 0 rather than
#: a negative pre-roll the extractor's ``-ss`` cannot express. Both defects made
#: the audio unprocessable end to end and were invisible to a structural probe;
#: the first synthetic ``vcp run`` exposed them.
_AUDIO_SAMPLE_RATE = 32000


#: Input-side flags that drop every non-deterministic muxing element (the
#: container's ``Date``/``writing app`` tags and encoder version stamps) so a
#: recipe's bytes are reproducible: the same recipe always builds the same file,
#: which is what lets the end-to-end bundle be byte-identical across runs.
_BITEXACT_INPUT: tuple[str, ...] = ("-fflags", "+bitexact")

#: The matching output-side flag; placed just before the output path.
_BITEXACT_OUTPUT = "-bitexact"


def _video_audio_argv(duration: int) -> tuple[str, ...]:
    return (
        "-hide_banner",
        "-nostdin",
        "-y",
        *_BITEXACT_INPUT,
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size=160x120:rate=8:duration={duration}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:sample_rate={_AUDIO_SAMPLE_RATE}:duration={duration}",
    )


def _subtitle_muxed_argv(duration: int) -> tuple[str, ...]:
    """Video + audio + a muxed subtitle input, all mapped into one Matroska file."""

    return (
        *_video_audio_argv(duration),
        "-i",
        _SUBTITLE_TOKEN,
        "-map",
        "0:v",
        "-map",
        "1:a",
        "-map",
        "2:s",
        "-c:v",
        "ffv1",
        "-c:a",
        "flac",
        "-c:s",
        "srt",
        _BITEXACT_OUTPUT,
        _OUTPUT_TOKEN,
    )


def _video_audio_only_argv(duration: int) -> tuple[str, ...]:
    return (
        *_video_audio_argv(duration),
        "-map",
        "0:v",
        "-map",
        "1:a",
        "-c:v",
        "ffv1",
        "-c:a",
        "flac",
        _BITEXACT_OUTPUT,
        _OUTPUT_TOKEN,
    )


def _visual_text_argv(duration: int) -> tuple[str, ...]:
    """Frames bearing a burned-in counter drawn by ``testsrc``'s built-in font."""

    return (
        "-hide_banner",
        "-nostdin",
        "-y",
        *_BITEXACT_INPUT,
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size=160x120:rate=8:duration={duration}",
        "-map",
        "0:v",
        "-c:v",
        "ffv1",
        _BITEXACT_OUTPUT,
        _OUTPUT_TOKEN,
    )


#: The five branches, mirroring Phase 11's mandatory real-video branches one for
#: one. Streams are recorded sorted so probe assertions are order-independent.
FIXTURE_RECIPES: tuple[FixtureRecipe, ...] = (
    FixtureRecipe(
        fixture_id="subtitle-first",
        description="Video with a muxed subtitle track (subtitle-first flow).",
        subtitle_source=_ORDINARY_SUBTITLES,
        builds=(
            MediaBuild(
                output="subtitle-first.mkv",
                argv=_subtitle_muxed_argv(3),
                expected_streams=("audio", "subtitle", "video"),
            ),
        ),
    ),
    FixtureRecipe(
        fixture_id="full-asr",
        description="Video and audio with no subtitle track (full-ASR flow).",
        subtitle_source=None,
        builds=(
            MediaBuild(
                output="full-asr.mkv",
                argv=_video_audio_only_argv(3),
                expected_streams=("audio", "video"),
            ),
        ),
    ),
    FixtureRecipe(
        fixture_id="anomalous-subtitles",
        description="Rolling-repeat, time-drifting subtitles muxed onto video.",
        subtitle_source=_ANOMALOUS_SUBTITLES,
        builds=(
            MediaBuild(
                output="anomalous-subtitles.mkv",
                argv=_subtitle_muxed_argv(4),
                expected_streams=("audio", "subtitle", "video"),
            ),
        ),
    ),
    FixtureRecipe(
        fixture_id="multi-part",
        description="A three-file multi-Part collection of video + audio.",
        subtitle_source=None,
        builds=(
            MediaBuild(
                output="multi-part-01.mkv",
                argv=_video_audio_only_argv(2),
                expected_streams=("audio", "video"),
            ),
            MediaBuild(
                output="multi-part-02.mkv",
                argv=_video_audio_only_argv(2),
                expected_streams=("audio", "video"),
            ),
            MediaBuild(
                output="multi-part-03.mkv",
                argv=_video_audio_only_argv(2),
                expected_streams=("audio", "video"),
            ),
        ),
    ),
    FixtureRecipe(
        fixture_id="visual-text",
        description="Text-bearing frames (burned-in testsrc counter) for OCR.",
        subtitle_source=None,
        builds=(
            MediaBuild(
                output="visual-text.mkv",
                argv=_visual_text_argv(3),
                expected_streams=("video",),
            ),
        ),
    ),
)


def resolve_fixture_toolchain(project_root: Path) -> FixtureToolchain:
    """Verify ffmpeg/ffprobe against ``config/tools.json`` and return their paths.

    Raises :class:`FixtureToolchainError` — never skips — when an entry is
    missing, its evidence is incomplete, the binary is absent, or its SHA-256 or
    ``-version`` line has drifted from the pinned identity.
    """

    registry = _load_tool_registry(project_root)
    ffmpeg_path, ffmpeg_version = _verify_tool(registry, "ffmpeg")
    ffprobe_path, ffprobe_version = _verify_tool(registry, "ffprobe")
    return FixtureToolchain(
        ffmpeg=ffmpeg_path,
        ffprobe=ffprobe_path,
        ffmpeg_version=ffmpeg_version,
        ffprobe_version=ffprobe_version,
    )


def generate_fixture(
    recipe: FixtureRecipe, toolchain: FixtureToolchain, cache_root: Path
) -> GeneratedFixture:
    """Build ``recipe``'s media under ``cache_root``, reusing an earlier build.

    All output stays under ``cache_root / vN / <fixture_id>``; nothing is written
    elsewhere. If a completion marker from an earlier call this session is
    present, no ffmpeg runs and ``regenerated`` is ``False``.
    """

    branch_dir = cache_root / f"v{RECIPES_VERSION}" / recipe.fixture_id
    marker = branch_dir / _COMPLETE_MARKER
    parts = tuple(branch_dir / build.output for build in recipe.builds)

    if marker.is_file() and all(part.is_file() for part in parts):
        return GeneratedFixture(recipe=recipe, parts=parts, regenerated=False)

    branch_dir.mkdir(parents=True, exist_ok=True)
    subtitle_path: Path | None = None
    if recipe.subtitle_source is not None:
        subtitle_path = branch_dir / "source.srt"
        subtitle_path.write_text(recipe.subtitle_source, encoding="utf-8")

    for build, part in zip(recipe.builds, parts, strict=True):
        _run_ffmpeg(toolchain, _resolve_argv(build.argv, subtitle_path, part), recipe.fixture_id)
        if not part.is_file() or part.stat().st_size == 0:
            raise FixtureGenerationError(
                "fixture_output_empty",
                f"ffmpeg produced no bytes for {recipe.fixture_id} part {build.output}.",
            )

    marker.write_text("ok\n", encoding="utf-8")
    return GeneratedFixture(recipe=recipe, parts=parts, regenerated=True)


def probe_document(path: Path, toolchain: FixtureToolchain) -> str:
    """Return the raw ``-show_streams`` JSON real ffprobe reports for ``path``.

    The single place the ffprobe argv is defined, so structure assertions and the
    production probe-projector twin both run the identical command.
    """

    result = subprocess.run(
        [
            str(toolchain.ffprobe),
            "-v",
            "error",
            "-of",
            "json",
            "-show_streams",
            str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise FixtureGenerationError(
            "ffprobe_failed",
            f"ffprobe exited {result.returncode} for {path.name}: {result.stderr.strip()}",
        )
    return result.stdout


def probe_stream_types(path: Path, toolchain: FixtureToolchain) -> tuple[str, ...]:
    """Return the sorted ``codec_type`` of every stream real ffprobe reports."""

    try:
        document = json.loads(probe_document(path, toolchain))
    except json.JSONDecodeError as error:
        raise FixtureGenerationError(
            "ffprobe_output_invalid", f"ffprobe emitted non-JSON for {path.name}."
        ) from error
    streams = document.get("streams")
    if not isinstance(streams, list):
        raise FixtureGenerationError(
            "ffprobe_output_invalid", f"ffprobe reported no streams list for {path.name}."
        )
    return tuple(sorted(str(stream.get("codec_type")) for stream in streams))


def _load_tool_registry(project_root: Path) -> Mapping[str, Mapping[str, object]]:
    registry_path = project_root / "config" / "tools.json"
    try:
        decoded = json.loads(registry_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise FixtureToolchainError(
            "tool_registry_missing", f"Tool registry is absent: {registry_path}"
        ) from error
    except (OSError, json.JSONDecodeError) as error:
        raise FixtureToolchainError(
            "tool_registry_invalid", f"Tool registry cannot be read: {registry_path}"
        ) from error
    tools = decoded.get("tools") if isinstance(decoded, Mapping) else None
    if not isinstance(tools, list):
        raise FixtureToolchainError("tool_registry_invalid", "Tool registry has no tools list.")
    registry: dict[str, Mapping[str, object]] = {}
    for tool in tools:
        if isinstance(tool, Mapping) and isinstance(tool.get("id"), str):
            registry[str(tool["id"])] = tool
    return registry


def _verify_tool(registry: Mapping[str, Mapping[str, object]], tool_id: str) -> tuple[Path, str]:
    entry = registry.get(tool_id)
    if entry is None:
        raise FixtureToolchainError("tool_entry_missing", f"Tool registry has no {tool_id} entry.")
    path_value = entry.get("path")
    expected_sha = entry.get("binary_sha256")
    expected_identity = entry.get("version_identity")
    if not (
        isinstance(path_value, str)
        and isinstance(expected_sha, str)
        and isinstance(expected_identity, str)
    ):
        raise FixtureToolchainError(
            "tool_evidence_incomplete",
            f"{tool_id} entry lacks path/binary_sha256/version_identity evidence.",
        )
    try:
        current = identify_external_tool(tool_id, Path(path_value))
    except (ExternalToolError, OSError) as error:
        raise FixtureToolchainError(
            "tool_absent", f"{tool_id} is not usable at {path_value}: {error}"
        ) from error
    if current.sha256 != expected_sha:
        raise FixtureToolchainError(
            "tool_identity_mismatch",
            f"{tool_id} SHA-256 changed: expected {expected_sha}, got {current.sha256}.",
        )
    if current.version != expected_identity:
        raise FixtureToolchainError(
            "tool_version_mismatch",
            f"{tool_id} version changed: expected {expected_identity!r}, got {current.version!r}.",
        )
    return current.path, current.version


def _resolve_argv(
    argv: tuple[str, ...], subtitle_path: Path | None, output_path: Path
) -> tuple[str, ...]:
    resolved: list[str] = []
    for token in argv:
        if token == _OUTPUT_TOKEN:
            resolved.append(str(output_path))
        elif token == _SUBTITLE_TOKEN:
            if subtitle_path is None:
                raise FixtureGenerationError(
                    "fixture_subtitle_missing",
                    "A recipe requested a subtitle input but declared no subtitle_source.",
                )
            resolved.append(str(subtitle_path))
        else:
            resolved.append(token)
    return tuple(resolved)


def _run_ffmpeg(toolchain: FixtureToolchain, argv: tuple[str, ...], fixture_id: str) -> None:
    result = subprocess.run(
        [str(toolchain.ffmpeg), *argv],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise FixtureGenerationError(
            "ffmpeg_failed",
            f"ffmpeg exited {result.returncode} building {fixture_id}: {result.stderr.strip()}",
        )
