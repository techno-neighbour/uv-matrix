"""Command-line interface for uv-matrix."""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from .config import (
    TASK_DEFS_TABLE,
    ConfigError,
    find_pyproject,
    iter_plan,
    load_config,
    parse_filters,
    validate_config_names,
)
from .evaluate import EvalError
from .runner import Job, TaskError, resolve_job

# Project-local directory holding the isolated per-job environments, à la tox's
# `.tox/<envname>`. Keeps each job's environment out of the developer's `.venv`.
ENV_DIR = ".uv-matrix"

_FILTER_HELP = "select jobs whose matrix cell matches KEY=VALUE (repeatable)"


def _version() -> str:
    try:
        return version("uv-matrix")
    except PackageNotFoundError:  # running from a source tree without an install
        return "0.0.0"


# ANSI SGR codes keyed by a short style name, used by `_Style` below.
_ANSI = {"bold": "1", "dim": "2", "red": "31", "green": "32", "cyan": "36"}


class _Style:
    """Wrap text in ANSI colors, or pass it through verbatim when disabled."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def __call__(self, name: str, text: str) -> str:
        if not self.enabled:
            return text
        return f"\033[{_ANSI[name]}m{text}\033[0m"


def _use_color(args: argparse.Namespace) -> bool:
    """Decide whether to emit ANSI color for this invocation.

    Color is off when ``--no-color`` is given, when ``NO_COLOR`` is set in the
    environment (see https://no-color.org/), or when stdout is not a TTY (e.g.
    piped or redirected), and on otherwise.
    """
    if getattr(args, "no_color", False) or os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def _verbosity(args: argparse.Namespace) -> int:
    """Net verbosity level: positive for ``-v``, negative for ``-q``."""
    return getattr(args, "verbose", 0) - getattr(args, "quiet", 0)


def _label(matrix_name: str, cell: dict[str, Any], task_name: str) -> str:
    cells = " ".join(f"{key}={value}" for key, value in cell.items())
    return f"{matrix_name}:{task_name} {cells}".rstrip()


def _cell_matches(cell: dict[str, Any], filters: dict[str, set[str]]) -> bool:
    return all(str(cell.get(key)) in values for key, values in filters.items())


def _selected(
    config: dict[str, Any], task_filter: str | None, filters: dict[str, set[str]]
) -> Iterator[tuple[str, dict[str, Any], str]]:
    """Yield ``(matrix_name, cell, task_name)`` passing the task and cell filters."""
    for matrix_name, cell, task_name in iter_plan(config):
        if task_filter is not None and task_name != task_filter:
            continue
        if not _cell_matches(cell, filters):
            continue
        yield matrix_name, cell, task_name


def _selected_for_run(
    config: dict[str, Any],
    matrix_filter: str | None,
    task_filter: str | None,
    filters: dict[str, set[str]],
) -> Iterator[tuple[str, dict[str, Any], str]]:
    """Yield ``(matrix_name, cell, task_name)`` selected by ``--matrix``/``--task``/``--filter``.

    ``--matrix`` and ``--task`` are matched by exact name; ``--filter`` matches
    against the cell's axis values. An unknown matrix or task name is an error
    (rather than a silent empty selection) so a typo is caught instead of quietly
    running nothing.
    """
    plan = list(iter_plan(config))
    if matrix_filter is not None:
        names = {name for name, _, _ in plan}
        if matrix_filter not in names:
            known = ", ".join(sorted(names)) or "(none)"
            raise ConfigError(f"unknown matrix {matrix_filter!r}; defined matrices: {known}")
    if task_filter is not None:
        tasks = {task for _, _, task in plan}
        if task_filter not in tasks:
            known = ", ".join(sorted(tasks)) or "(none)"
            raise ConfigError(f"unknown task {task_filter!r}; available tasks: {known}")
    for matrix_name, cell, task_name in plan:
        if matrix_filter is not None and matrix_name != matrix_filter:
            continue
        if task_filter is not None and task_name != task_filter:
            continue
        if not _cell_matches(cell, filters):
            continue
        yield matrix_name, cell, task_name


def _job_env(job: Job, root: Path) -> dict[str, str]:
    """Subprocess environment, with per-job isolation layered under task env."""
    env = {**os.environ}
    env["UV_PROJECT_ENVIRONMENT"] = str(root / ENV_DIR / job.env_key)
    env.update(job.env)  # an explicit task `env` always wins
    return env


def _cmd_list(config: dict[str, Any], args: argparse.Namespace, root: Path) -> int:
    """List the selectable jobs (matrix:task + cell) without evaluating fields.

    `list` is a selection aid: it only expands the matrices and never evaluates
    `when`, templates, or expressions — that happens at `run` time.
    """
    style = _Style(_use_color(args))
    filters = parse_filters(config, args.filter or [])
    for matrix_name, cell, task_name in _selected(config, args.task, filters):
        print(style("cyan", _label(matrix_name, cell, task_name)))
    return 0


def _parallelism(config: dict[str, Any], args: argparse.Namespace) -> int:
    """Resolve how many jobs to run at once. ``--max-jobs`` overrides ``max-jobs``.

    Returns at least 1; ``1`` means sequential (the default), which keeps stdio
    inherited and captures nothing.
    """
    raw = args.max_jobs if args.max_jobs is not None else config.get("max-jobs", 1)
    try:
        n = int(raw)
    except (TypeError, ValueError):
        raise ConfigError(f"max-jobs must be an integer, got {raw!r}")
    return max(1, n)


def _print_job_banner(job: Job, style: _Style, verbosity: int) -> None:
    if verbosity >= 0:
        print(style("bold", f"==> {job.label}"))
        print(style("dim", f"  + {job.command_str}"))
        print(style("dim", f"  env: {ENV_DIR}/{job.env_key}"))


def _emit_output(output: str | None) -> None:
    """Write a job's captured output as a block, ensuring a trailing newline.

    Unlike the banner this is the subprocess's own output, so it is printed
    regardless of verbosity — the same output a sequential run streams live.
    """
    if output:
        sys.stdout.write(output)
        if not output.endswith("\n"):
            sys.stdout.write("\n")


def _record_result(
    job: Job,
    returncode: int,
    style: _Style,
    failed: list[tuple[Job, int]],
) -> bool:
    """Record a finished job's exit code; return True when the run should stop.

    A non-zero exit is always a failure and always counts toward the final exit
    code. When the failing job's ``continue-on-error`` is true the run goes on to
    the remaining jobs; otherwise the run stops after this failure.
    """
    if returncode == 0:
        return False
    failed.append((job, returncode))
    if job.continue_on_error:
        print(style("red", f"  -> failed: exit {returncode} (continuing)"))
        return False
    print(style("red", f"  -> failed: exit {returncode} (stopping)"))
    return True


def _run_sequential(
    runnable: list[Job], root: Path, style: _Style, verbosity: int
) -> list[tuple[Job, int]]:
    """Run jobs one at a time, inheriting stdio so output streams live (no capture)."""
    failed: list[tuple[Job, int]] = []
    for job in runnable:
        _print_job_banner(job, style, verbosity)
        env = _job_env(job, root)
        result = subprocess.run(job.command, env=env, cwd=job.cwd)
        if _record_result(job, result.returncode, style, failed):
            break
    return failed


def _run_parallel(
    runnable: list[Job],
    root: Path,
    parallel: int,
    style: _Style,
    verbosity: int,
) -> list[tuple[Job, int]]:
    """Run up to ``parallel`` jobs at once, capturing each job's output.

    Output is captured per job (stderr folded into stdout) and printed as a
    single block after the job finishes, so concurrent jobs never interleave and
    each block is identifiable by its banner. Jobs sharing an environment key are
    serialized via a per-key lock so their ``uv sync`` calls do not race on the
    same directory.

    A failing job whose ``continue-on-error`` is false cancels jobs that have not
    started yet; jobs already running are allowed to finish and their output is
    still reported.
    """
    env_locks: dict[str, threading.Lock] = {}
    locks_guard = threading.Lock()

    def env_lock(key: str) -> threading.Lock:
        with locks_guard:
            return env_locks.setdefault(key, threading.Lock())

    def run_one(job: Job) -> subprocess.CompletedProcess[str]:
        env = _job_env(job, root)
        with env_lock(job.env_key):
            return subprocess.run(
                job.command,
                env=env,
                cwd=job.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )

    failed: list[tuple[Job, int]] = []
    stopping = False
    with concurrent.futures.ThreadPoolExecutor(max_workers=parallel) as executor:
        futures = {executor.submit(run_one, job): job for job in runnable}
        for future in concurrent.futures.as_completed(futures):
            job = futures[future]
            try:
                result = future.result()
            except concurrent.futures.CancelledError:
                continue
            _print_job_banner(job, style, verbosity)
            _emit_output(result.stdout)
            if _record_result(job, result.returncode, style, failed) and not stopping:
                stopping = True
                for pending in futures:
                    pending.cancel()
    return failed


def _cmd_run(config: dict[str, Any], args: argparse.Namespace, root: Path) -> int:
    style = _Style(_use_color(args))
    verbosity = _verbosity(args)
    task_defs = config.get(TASK_DEFS_TABLE, {})
    runnable: list[Job] = []
    posargs = getattr(args, "posargs", [])
    filters = parse_filters(config, args.filter or [])
    skipped: list[str] = []
    for matrix_name, cell, task_name in _selected_for_run(config, args.matrix, args.task, filters):
        job = resolve_job(config, matrix_name, cell, task_name, task_defs, posargs)
        if job is not None:
            runnable.append(job)
        else:
            # A `when` that evaluated false. Recorded so the summary can report a
            # skip count; the per-job line below is only surfaced under -v, since a
            # skip is a routine, expected outcome rather than something to report.
            skipped.append(_label(matrix_name, cell, task_name))

    if not runnable and not skipped:
        print("No jobs to run.")
        return 0

    parallel = _parallelism(config, args)

    if verbosity >= 1:
        for label in skipped:
            print(style("dim", f"-- skipped (when): {label}"))

    failed: list[tuple[Job, int]] = []
    if args.dry_run:
        for job in runnable:
            _print_job_banner(job, style, verbosity)
    elif parallel > 1:
        failed = _run_parallel(runnable, root, parallel, style, verbosity)
    else:
        failed = _run_sequential(runnable, root, style, verbosity)

    if verbosity >= 0:
        print()
        if skipped:
            # The skip count is part of the summary regardless of -v, so a `when`
            # exclusion is never invisible (issue #7); the per-job lines above stay -v-only.
            noun = "job" if len(skipped) == 1 else "jobs"
            print(style("dim", f"{len(skipped)} {noun} skipped (when)"))
    if failed:
        print(style("red", "Failed jobs:"))
        for job, code in failed:
            print(style("red", f"  - {job.label}: exit {code}"))
        return 1
    if verbosity >= 0:
        print(style("green", "All jobs passed."))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="uv-matrix",
        description="Matrix runner for uv projects.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {_version()}",
        help="show the program version and exit",
    )

    # Options shared by every subcommand. Kept on a parent parser (rather than
    # the top-level parser) so they may follow the subcommand, e.g.
    # `uv-matrix run -v --no-color`.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-c",
        "--config",
        metavar="PATH",
        help="path to the pyproject.toml to read (default: discovered like uv)",
    )
    common.add_argument(
        "-p",
        "--project",
        metavar="DIR",
        help="project root to operate in (default: the config file's directory)",
    )
    common.add_argument(
        "-v", "--verbose", action="count", default=0, help="increase verbosity (repeatable)"
    )
    common.add_argument(
        "-q", "--quiet", action="count", default=0, help="decrease verbosity (repeatable)"
    )
    common.add_argument(
        "-o",
        "--no-color",
        action="store_true",
        help="disable colored output (also honors the NO_COLOR environment variable)",
    )

    sub = parser.add_subparsers(dest="command")

    run_p = sub.add_parser("run", parents=[common], help="expand the matrices and run jobs")
    run_p.add_argument("-m", "--matrix", metavar="NAME", help="run only the matrix with this name")
    run_p.add_argument("-t", "--task", metavar="NAME", help="run only the task with this name")
    run_p.add_argument(
        "-f", "--filter", action="append", metavar="KEY=VALUE", help=_FILTER_HELP
    )
    run_p.add_argument(
        "-d", "--dry-run", action="store_true", help="print commands without running them"
    )
    run_p.add_argument(
        "-n",
        "--max-jobs",
        dest="max_jobs",
        type=int,
        metavar="N",
        help=(
            "run up to N jobs concurrently (overrides max-jobs); "
            "parallel runs capture each job's output, sequential runs stream it live"
        ),
    )
    run_p.epilog = (
        "arguments after -- are exposed to templates as {{ posargs }} (e.g. run -- -k slow)"
    )
    run_p.set_defaults(func=_cmd_run)

    list_p = sub.add_parser(
        "list", parents=[common], help="list selectable jobs without evaluating or running them"
    )
    list_p.add_argument("task", nargs="?", help="show only this task")
    list_p.add_argument(
        "-f", "--filter", action="append", metavar="KEY=VALUE", help=_FILTER_HELP
    )
    list_p.set_defaults(func=_cmd_list)

    return parser


def _split_posargs(argv: list[str]) -> tuple[list[str], list[str]]:
    """Split argv at the first ``--``: everything after it becomes posargs.

    Done before argparse sees the arguments so the separator cleanly divides
    uv-matrix's own options from the pass-through ``{{ posargs }}`` (e.g.
    ``run -- -k foo`` or ``run --task test -- -k foo``).
    """
    if "--" in argv:
        index = argv.index("--")
        return argv[:index], argv[index + 1 :]
    return argv, []


def _locate_project(args: argparse.Namespace) -> tuple[Path, Path]:
    """Resolve the ``(pyproject, root)`` pair from ``--config``/``--project``.

    With neither flag, the config file is discovered the way uv does (walking up
    from the cwd) and the project root is its directory. ``--config`` names the
    config file explicitly; ``--project`` names the project root (and, on its
    own, selects ``DIR/pyproject.toml``).
    """
    if args.config is not None:
        pyproject = Path(args.config)
        if not pyproject.is_file():
            raise ConfigError(f"{pyproject}: not found")
        root = Path(args.project) if args.project is not None else pyproject.parent
    elif args.project is not None:
        root = Path(args.project)
        pyproject = root / "pyproject.toml"
        if not pyproject.is_file():
            raise ConfigError(f"{pyproject}: not found")
    else:
        pyproject = find_pyproject()
        root = pyproject.parent
    return pyproject.resolve(), root.resolve()


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    argv, posargs = _split_posargs(argv)
    parser = _build_parser()
    args = parser.parse_args(argv)
    args.posargs = posargs
    if args.command is None:
        parser.print_help()
        return 1

    try:
        pyproject, root = _locate_project(args)
        # Operate from the project root (like uv/tox), so relative paths in a
        # task's `run`/`cwd` resolve the same wherever uv-matrix is invoked.
        if Path.cwd().resolve() != root:
            os.chdir(root)
        config = load_config(pyproject)
        validate_config_names(config)
        return args.func(config, args, root)
    except (ConfigError, TaskError, EvalError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
