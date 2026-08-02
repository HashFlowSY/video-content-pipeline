"""Minimal Phase 1 command-line boundary."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from video_content_pipeline import __version__
from video_content_pipeline.environment import assert_project_venv, assert_runtime_policy


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vcp")
    parser.add_argument("--version", action="version", version=__version__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("check-environment")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the intentionally small Phase 1 CLI."""

    arguments = _parser().parse_args(argv)
    if arguments.command == "check-environment":
        assert_runtime_policy()
        identity = assert_project_venv()
        print(
            json.dumps(
                {
                    "executable": str(identity.executable),
                    "prefix": str(identity.prefix),
                    "virtual_env": str(identity.virtual_env),
                },
                sort_keys=True,
            )
        )
        return 0
    raise AssertionError(f"Unhandled command: {arguments.command}")
