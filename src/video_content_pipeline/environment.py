"""Project-local Python environment validation."""

from __future__ import annotations

import os
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


class EnvironmentGateError(RuntimeError):
    """Raised when the current process is not using the project environment."""


@dataclass(frozen=True)
class EnvironmentIdentity:
    """The paths that identify an activated project virtual environment."""

    virtual_env: Path
    prefix: Path
    executable: Path


@dataclass(frozen=True)
class RuntimePolicy:
    """Validated Phase 1 runtime policy values."""

    project_root: Path
    virtual_env: Path
    allow_system_python: bool
    allow_ordinary_pip_install: bool
    allow_uv_run: bool
    automatic_dependency_upgrade: bool
    automatic_python_download: bool
    allow_runtime_network: bool
    allow_automatic_model_downloads: bool
    allow_paid_apis: bool


def project_root() -> Path:
    """Return the repository root from the source-layout package location."""

    return Path(__file__).resolve().parents[2]


def assert_project_venv() -> EnvironmentIdentity:
    """Return the active project environment identity or raise a clear error."""

    root = project_root()
    expected_venv = root / ".venv"
    expected_executables = {
        expected_venv / "bin" / "python",
        expected_venv / "bin" / "python3",
        expected_venv / "bin" / f"python{sys.version_info.major}.{sys.version_info.minor}",
    }
    virtual_env = os.environ.get("VIRTUAL_ENV")

    if virtual_env != str(expected_venv):
        raise EnvironmentGateError(
            f"VIRTUAL_ENV must equal {expected_venv}; received {virtual_env!r}."
        )

    prefix = Path(sys.prefix)
    if prefix.resolve() != expected_venv:
        raise EnvironmentGateError(f"sys.prefix must equal {expected_venv}; received {prefix}.")

    executable = Path(sys.executable)
    if executable not in expected_executables:
        raise EnvironmentGateError(
            f"sys.executable must be a project virtual-environment alias; received {executable}."
        )

    if not executable.resolve().is_relative_to(root):
        raise EnvironmentGateError(f"sys.executable target must stay below {root}.")

    return EnvironmentIdentity(
        virtual_env=expected_venv,
        prefix=prefix,
        executable=executable,
    )


def _required_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise EnvironmentGateError(f"{label} must be a mapping.")
    return value


def _required_bool(section: Mapping[str, object], key: str) -> bool:
    value = section.get(key)
    if not isinstance(value, bool):
        raise EnvironmentGateError(f"{key} must be a boolean.")
    return value


def _project_path(root: Path, value: object, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise EnvironmentGateError(f"{label} must be a non-empty relative path.")
    path = (root / value).resolve()
    if not path.is_relative_to(root):
        raise EnvironmentGateError(f"{label} must stay below {root}; received {value!r}.")
    return path


def assert_runtime_policy() -> RuntimePolicy:
    """Validate the checked-in policy that prevents global and automatic behavior."""

    root = project_root()
    policy_path = root / "config" / "runtime-policy.toml"
    with policy_path.open("rb") as policy_file:
        document: Mapping[str, object] = tomllib.load(policy_file)

    paths = _required_mapping(document.get("paths"), "paths")
    execution = _required_mapping(document.get("execution"), "execution")
    network = _required_mapping(document.get("network"), "network")
    virtual_env = _project_path(
        root,
        document.get("project_virtual_environment"),
        "project_virtual_environment",
    )
    if virtual_env != root / ".venv":
        raise EnvironmentGateError(f"project_virtual_environment must equal {root / '.venv'}.")

    for key in (
        "uv_binary",
        "python_install_dir",
        "uv_cache_dir",
        "python_cache_dir",
        "temporary_dir",
        "model_registry",
        "input_dir",
        "work_dir",
        "output_dir",
        "plans_dir",
    ):
        _project_path(root, paths.get(key), f"paths.{key}")

    require_venv = _required_bool(execution, "require_project_virtual_environment")
    allow_uv_run = _required_bool(execution, "allow_uv_run")
    allow_ordinary_pip_install = _required_bool(execution, "allow_ordinary_pip_install")
    allow_system_python = _required_bool(execution, "allow_system_python")
    automatic_dependency_upgrade = _required_bool(execution, "automatic_dependency_upgrade")
    automatic_python_download = _required_bool(execution, "automatic_python_download")
    allow_runtime_network = _required_bool(network, "allow_runtime_network")
    allow_automatic_model_downloads = _required_bool(network, "allow_automatic_model_downloads")
    allow_paid_apis = _required_bool(network, "allow_paid_apis")

    if not require_venv:
        raise EnvironmentGateError("Project virtual environments must be required.")
    if any(
        (
            allow_uv_run,
            allow_ordinary_pip_install,
            allow_system_python,
            automatic_dependency_upgrade,
            automatic_python_download,
            allow_runtime_network,
            allow_automatic_model_downloads,
            allow_paid_apis,
        )
    ):
        raise EnvironmentGateError("Phase 1 runtime policy permits forbidden automatic behavior.")

    return RuntimePolicy(
        project_root=root,
        virtual_env=virtual_env,
        allow_system_python=allow_system_python,
        allow_ordinary_pip_install=allow_ordinary_pip_install,
        allow_uv_run=allow_uv_run,
        automatic_dependency_upgrade=automatic_dependency_upgrade,
        automatic_python_download=automatic_python_download,
        allow_runtime_network=allow_runtime_network,
        allow_automatic_model_downloads=allow_automatic_model_downloads,
        allow_paid_apis=allow_paid_apis,
    )
