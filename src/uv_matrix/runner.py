"""Resolve matrix jobs into uv commands and execute them."""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from .evaluate import build_context, eval_expr, render_string, render_template

_UNSAFE = re.compile(r"[^A-Za-z0-9.+_-]")


def _shell_command(run: str) -> list[str]:
    """Wrap ``run`` for the platform's default shell.

    Mirrors how :mod:`subprocess` resolves ``shell=True``: on Windows it
    invokes ``%COMSPEC%`` (``cmd.exe`` when unset) with ``/c``; everywhere
    else it uses ``sh -c``. The shell is run inside the uv environment by
    ``uv run``, so ``run`` keeps full shell syntax (pipes, ``&&``,
    redirects, variable expansion) on each OS.
    """
    if sys.platform == "win32":
        comspec = os.environ.get("COMSPEC", "cmd.exe")
        return [comspec, "/c", run]
    return ["sh", "-c", run]


class TaskError(Exception):
    """Raised when a job references an undefined or invalid task."""


@dataclass
class Job:
    """A single resolved job (a matrix cell paired with a task)."""

    matrix_name: str
    task: str
    matrix: dict[str, Any]
    python_version: str | None
    command: list[str]
    env: dict[str, str]
    cwd: str | None
    continue_on_error: bool
    env_key: str

    @property
    def label(self) -> str:
        """Human-readable ``matrix:task key=value ...`` description."""
        cells = " ".join(f"{key}={value}" for key, value in self.matrix.items())
        return f"{self.matrix_name}:{self.task} {cells}".rstrip()

    @property
    def command_str(self) -> str:
        return " ".join(shlex.quote(part) for part in self.command)


def _str_list(value: Any, task_name: str, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise TaskError(f"task {task_name!r}: {field!r} must be an array")
    return value


def _rendered_list(
    task_config: dict[str, Any], field: str, task_name: str, ctx: dict[str, Any]
) -> list[str]:
    """Render a list field and drop elements that are empty after stripping.

    A template such as ``"{{ matrix['django'] or '' }}"`` evaluates to an empty
    string when the value is absent. Emitting it verbatim would build a bogus
    flag (e.g. ``--group ""``), so each rendered element is stripped and skipped
    when it has no remaining content. This lets a template conditionally omit an
    element by evaluating to ``""``.
    """
    rendered = render_template(_str_list(task_config.get(field, []), task_name, field), ctx)
    result = []
    for item in rendered:
        text = str(item).strip()
        if text:
            result.append(text)
    return result


def _env_key(
    python_version: str | None, groups: list[str], extras: list[str], uv_args: list[str]
) -> str:
    """Stable directory name for the isolated environment of this job.

    Keyed by everything that determines the environment's contents, so jobs that
    resolve to the same environment share one directory and different ones never
    collide. The Python version is kept readable; the rest goes into a hash.
    When no version is pinned, ``default`` stands in for uv's chosen interpreter.
    """
    raw = repr((python_version, groups, extras, uv_args))
    digest = hashlib.sha256(raw.encode()).hexdigest()[:8]
    label = _UNSAFE.sub("_", f"py{python_version or 'default'}")
    return f"{label}-{digest}"


def _load_envfiles(
    task_config: dict[str, Any], task_name: str, ctx: dict[str, Any]
) -> dict[str, str]:
    """Load the task's ``envfile`` paths into a flat ``{name: value}`` mapping.

    ``envfile`` is either a single path or a list of paths, each rendered as a
    Jinja2 template (so a path may use ``matrix``/``vars``/``environ``). Files are
    parsed in order with ``.env`` semantics; a later file overrides an earlier one
    on a shared key, so ``env`` (applied on top by the caller) always wins last.

    A path that does not name an existing file is an error rather than a silent
    skip — ``dotenv_values`` returns ``{}`` for a missing file, so the existence
    check is made here. A value with no ``=`` right-hand side parses to ``None``
    and is normalized to the empty string.

    Relative paths resolve from the current working directory, which the CLI sets
    to the project root, so an ``envfile`` resolves the same as ``run``/``cwd``.
    """
    raw = task_config.get("envfile")
    if raw is None:
        return {}
    if isinstance(raw, str):
        paths = [raw]
    elif isinstance(raw, list):
        paths = raw
    else:
        raise TaskError(f"task {task_name!r}: 'envfile' must be a string or an array")

    result: dict[str, str] = {}
    for entry in paths:
        path = render_string(entry, ctx)
        if not Path(path).is_file():
            raise TaskError(f"task {task_name!r}: envfile {path!r} not found")
        for key, value in dotenv_values(path).items():
            result[key] = value if value is not None else ""
    return result


def resolve_job(
    config: dict[str, Any],
    matrix_name: str,
    cell: dict[str, Any],
    task_name: str,
    task_defs: dict[str, Any],
    posargs: list[str] | None = None,
) -> Job | None:
    """Resolve a (matrix cell, task) pair into a :class:`Job`.

    Returns ``None`` when the task's ``when`` expression is false.

    ``posargs`` are the command-line arguments after ``--``, exposed to
    templates as ``{{ posargs }}``.
    """
    try:
        task_config = task_defs[task_name]
    except (KeyError, TypeError):
        raise TaskError(f"undefined task {task_name!r}")

    ctx = build_context(config, matrix_name, cell, task_name, task_config, posargs)

    # Environment is settled first, before any other field is evaluated: load the
    # `envfile`(s), then layer the rendered `env` on top (so `env` overrides a key
    # from a file), then fold the result into the `environ` namespace. Every field
    # below — `when` included — therefore reads the post-override values through
    # `{{ environ['X'] }}`. Precedence low→high: os.environ < envfile < env.
    envfile_vars = _load_envfiles(task_config, task_name, ctx)
    ctx["environ"] = {**os.environ, **envfile_vars}  # so `env` can read envfile values
    rendered_env = {
        str(key): render_string(value, ctx) for key, value in task_config.get("env", {}).items()
    }
    env = {**envfile_vars, **rendered_env}
    ctx["environ"] = {**os.environ, **env}

    if "when" in task_config and not eval_expr(task_config["when"], ctx):
        return None

    # `python-version` is a reserved matrix axis name. A task uses its own
    # `python-version` when set (rendered as a template); otherwise it inherits
    # the value from the matrix cell's `python-version` axis. When neither
    # supplies one, the job runs without `--python` and uv picks its default.
    if "python-version" in task_config:
        python_version = render_string(task_config["python-version"], ctx)
    elif "python-version" in cell:
        python_version = str(cell["python-version"])
    else:
        python_version = None

    if "run" not in task_config:
        raise TaskError(f"task {task_name!r}: missing 'run'")
    run = render_string(task_config["run"], ctx)

    groups = _rendered_list(task_config, "groups", task_name, ctx)
    extras = _rendered_list(task_config, "extras", task_name, ctx)
    uv_args = _rendered_list(task_config, "uv-args", task_name, ctx)

    command = ["uv", "run"]
    if python_version is not None:
        command += ["--python", python_version]
    for group in groups:
        command += ["--group", group]
    for extra in extras:
        command += ["--extra", extra]
    # Arbitrary uv flags (e.g. --with, --no-default-groups) passed through verbatim.
    command += uv_args
    # `run` is executed by a shell inside the uv environment, so shell syntax
    # (pipes, &&, redirects, variable expansion) all apply with the env's tools.
    # The shell is chosen per-OS (sh on POSIX, cmd.exe on Windows).
    command += _shell_command(run)

    cwd = render_string(task_config["cwd"], ctx) if "cwd" in task_config else None
    # The task's own `continue-on-error` wins; otherwise the global
    # [tool.uv-matrix] default applies; otherwise false (stop on this failure).
    coe = task_config.get("continue-on-error", config.get("continue-on-error", False))
    continue_on_error = bool(eval_expr(coe, ctx))

    return Job(
        matrix_name=matrix_name,
        task=task_name,
        matrix=cell,
        python_version=python_version,
        command=command,
        env=env,
        cwd=cwd,
        continue_on_error=continue_on_error,
        env_key=_env_key(python_version, groups, extras, uv_args),
    )
