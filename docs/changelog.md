# Changelog

## 0.1.0 — unreleased

Early development. The core works:

- Reads `[tool.uv-matrix]` from `pyproject.toml`.
- Expands named matrices into the cartesian product of their axes, paired with
  the tasks listed in each matrix.
- Resolves each `(cell, task)` pair into a command and runs it.
- `run` and `list` subcommands. `run` selects with `--matrix NAME` / `--task
  NAME` (unknown names error) and takes `--dry-run`; `list` only expands the
  matrices and evaluates nothing.
- `--filter KEY=VALUE` (on both `run` and `list`, repeatable) selects jobs by
  matrix-cell value: repeating a key ORs its values, different keys AND, and an
  unknown key or value errors. `--filter KEY=` (empty value) selects cells whose
  value is the empty string. Matrix axis names may use any character except
  whitespace and `=`, keeping the `KEY=VALUE` split unambiguous.
- Per-job isolated environments under `.uv-matrix/<key>/`
  (via `UV_PROJECT_ENVIRONMENT`), analogous to tox's `.tox/<envname>/`.
- `when`, `continue-on-error`, `env`, `cwd`, `groups`, `extras`, and
  `uv-args` task fields. In `groups`, `extras`, and `uv-args`, an element that
  renders to an empty or whitespace-only string is dropped, so a template can
  conditionally omit an element by evaluating to `""`.
- Task definitions live in `[tool.uv-matrix.tasks.<name>]` (plural), matching the
  `tasks = [...]` list inside a matrix.
- Template fields (`python-version`, `run`, `cwd`, `groups`, `extras`,
  `uv-args`, `env`) are rendered with Jinja2, so values use `{{ ... }}`
  placeholders (e.g. `python-version = "{{ matrix['python'] }}"`). `when` and
  `continue-on-error` remain Python expressions.
- `posargs` passthrough: arguments after `--` (`run -- -k slow`) are exposed to
  templates as a shell-quoted `{{ posargs }}` string, empty when none are given.
- `environ`: a copy of the process environment (`os.environ`) is exposed to
  templates and expressions, e.g. `{{ environ['HOME'] }}` or
  `when = "environ.get('CI')"`.
- `continue-on-error` (settable per task and globally in `[tool.uv-matrix]`)
  controls whether a failing job stops the run (`false`, the default) or lets the
  remaining jobs run (`true`); a failure always counts toward the exit code.
- The `run` summary reports how many jobs were skipped by `when`, and `-v` names
  each skipped job, so a `when` exclusion is no longer invisible.
- Parallel execution via `--max-jobs N` / `max-jobs` (default sequential).
  Parallel runs capture each job's output and print it as a per-job block; jobs
  sharing an environment are serialized; sequential runs still stream output
  live without capture.
