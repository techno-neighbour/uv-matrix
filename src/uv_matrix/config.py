"""Load uv-matrix configuration from ``pyproject.toml`` and expand it into jobs."""

from __future__ import annotations

import itertools
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # Python 3.10 has no stdlib tomllib; tomli is its backport.
    import tomli as tomllib

CONFIG_TABLE = "uv-matrix"

# Reserved key inside a matrix table: the list of task names to run for that
# matrix. Every other key in a matrix table is a matrix axis.
TASKS_KEY = "tasks"

# Table that holds the task definitions: [tool.uv-matrix.tasks.<name>]. It shares
# the "tasks" spelling with the matrix reserved key above so there is a single
# word to remember; the two live in different parent tables and never collide.
TASK_DEFS_TABLE = "tasks"

# A matrix axis name may use any character except whitespace and '='. '=' is the
# KEY=VALUE separator for `--filter`, and whitespace would be ambiguous on the
# command line and in the `key=value` job labels, so both are rejected.
_INVALID_AXIS_NAME = re.compile(r"[\s=]")


class ConfigError(Exception):
    """Raised when the uv-matrix configuration is missing or invalid."""


def validate_axis_name(name: str) -> str:
    """Return ``name`` unchanged, or raise ``ConfigError`` if it is not a valid axis name.

    Axis names must be non-empty and contain neither whitespace nor ``=`` so they
    stay unambiguous as the key in a ``--filter KEY=VALUE`` selector.
    """
    if not name or _INVALID_AXIS_NAME.search(name):
        raise ConfigError(
            f"invalid matrix axis name {name!r}: axis names must be non-empty and "
            f"must not contain whitespace or '='"
        )
    return name


def matrix_axes(matrix_def: dict[str, Any]) -> dict[str, Any]:
    """Return a matrix table's axes: every key except the reserved ``tasks``.

    Each axis name is validated, so callers reading axes consistently reject a
    name containing whitespace or ``=``.
    """
    return {
        validate_axis_name(key): value for key, value in matrix_def.items() if key != TASKS_KEY
    }


def find_pyproject(start: Path | str | None = None) -> Path:
    """Locate the nearest ``pyproject.toml`` by walking up the directory tree.

    This mirrors how uv discovers a project: it searches the current working
    directory and each of its parents, using the first ``pyproject.toml`` it
    finds. uv-matrix follows the same rule so it always operates on the file
    that ``uv run`` would.
    """
    start = Path(start) if start is not None else Path.cwd()
    start = start.resolve()
    for directory in (start, *start.parents):
        candidate = directory / "pyproject.toml"
        if candidate.is_file():
            return candidate
    raise ConfigError("no pyproject.toml found in the current directory or any parent directory")


def load_config(pyproject: Path | str | None = None) -> dict[str, Any]:
    """Read the ``[tool.uv-matrix]`` table from a ``pyproject.toml`` file.

    When no path is given, the file is discovered the same way uv finds a
    project: by searching the current directory and its parents.
    """
    path = Path(pyproject) if pyproject is not None else find_pyproject()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"{path}: not found") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML: {exc}") from exc

    try:
        return data["tool"][CONFIG_TABLE]
    except (KeyError, TypeError):
        raise ConfigError(f"{path}: missing [tool.{CONFIG_TABLE}] table") from None


def expand_matrix(axes: dict[str, Any]) -> list[dict[str, Any]]:
    """Expand matrix axes into their cartesian product.

    ``axes`` must already have the reserved ``tasks`` key removed; callers
    strip it before calling.
    """
    for key, value in axes.items():
        if not isinstance(value, list):
            raise ConfigError(f"matrix axis {key!r} must be an array")

    keys = list(axes)
    return [dict(zip(keys, combo)) for combo in itertools.product(*(axes[key] for key in keys))]


def iter_plan(config: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any], str]]:
    """Yield ``(matrix_name, cell, task_name)`` for every job in the config.

    Each named matrix is expanded independently: its axes (every key except the
    reserved ``tasks``) form a cartesian product, and each resulting cell is
    paired with each task name listed in ``tasks``.
    """
    matrices = config.get("matrix", {})
    if not matrices:
        raise ConfigError("no matrices defined ([tool.uv-matrix.matrix.<name>])")

    for matrix_name, matrix_def in matrices.items():
        if not isinstance(matrix_def, dict):
            raise ConfigError(f"matrix {matrix_name!r} must be a table")
        if TASKS_KEY not in matrix_def:
            raise ConfigError(f"matrix {matrix_name!r}: missing 'tasks'")
        task_names = matrix_def[TASKS_KEY]
        if not isinstance(task_names, list):
            raise ConfigError(f"matrix {matrix_name!r}: 'tasks' must be an array")

        axes = matrix_axes(matrix_def)
        for cell in expand_matrix(axes):
            for task_name in task_names:
                yield matrix_name, cell, task_name


def axis_values(config: dict[str, Any]) -> dict[str, set[str]]:
    """Map each matrix axis name to the set of its values (as strings) across all matrices."""
    result: dict[str, set[str]] = {}
    for matrix_def in config.get("matrix", {}).values():
        if not isinstance(matrix_def, dict):
            continue
        for key, values in matrix_axes(matrix_def).items():
            if not isinstance(values, list):
                continue
            result.setdefault(key, set()).update(str(v) for v in values)
    return result


def parse_filters(config: dict[str, Any], raw_filters: list[str]) -> dict[str, set[str]]:
    """Parse and validate ``key=value`` selection filters against the matrix axes.

    Filters group by key: a job is selected when, for every filtered key, its
    cell's value is among the values given for that key (OR within a key, AND
    across keys). An unknown key or value is an error rather than a silent
    no-match, so a typo is caught instead of quietly selecting nothing.

    The key and value are split on the first ``=``; since axis names never
    contain ``=``, a value may itself contain one (e.g. ``--filter
    expr=a==b``). The value after the ``=`` may be empty, so ``--filter axis=``
    selects cells whose axis value is the empty string (valid only when ``""``
    is one of the axis values).
    """
    axes = axis_values(config)
    filters: dict[str, set[str]] = {}
    for raw in raw_filters:
        key, sep, value = raw.partition("=")
        if not sep:
            raise ConfigError(f"invalid filter {raw!r}: expected KEY=VALUE")
        if key not in axes:
            known = ", ".join(sorted(axes)) or "(none)"
            raise ConfigError(f"unknown filter key {key!r}; known axes: {known}")
        if value not in axes[key]:
            known = ", ".join(sorted(axes[key]))
            raise ConfigError(f"unknown value {value!r} for filter key {key!r}; values: {known}")
        filters.setdefault(key, set()).add(value)
    return filters
