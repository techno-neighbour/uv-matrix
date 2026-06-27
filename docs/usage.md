# Usage

`uv-matrix` has two subcommands: `run`, which executes the expanded jobs, and
`list`, which prints the selectable jobs without evaluating or running anything.

`uv-matrix` finds `pyproject.toml` the same way uv does: it searches the current
directory and walks up through its parents, using the first `pyproject.toml` it
finds. So you can run it from anywhere inside a project, just like uv —
uv-matrix changes to that project root first, so relative paths in a task's
`run` and `cwd` resolve the same wherever you invoke it. Use `--config` /
`--project` (below) to point it somewhere else.

```bash
uv-matrix --version        # print the version and exit
```

## Commands

```bash
uv-matrix run                 # expand the matrices and run every job
uv-matrix run --task test     # run only the "test" task
uv-matrix run --matrix checks # run only the "checks" matrix
uv-matrix run --filter python-version=3.13  # run only cells where python-version is 3.13
uv-matrix run --max-jobs 4    # run up to 4 jobs at once
uv-matrix run --dry-run       # print the commands without running them
uv-matrix run --task test -- -k slow  # pass args after -- to each job's {{ posargs }}
uv-matrix list                # list selectable jobs (no evaluation, no run)
```

### `run`

Expands every matrix, resolves each `(cell, task)` pair into a job, and runs the
jobs sequentially. Each job prints its name and the environment it runs in:

```text
==> test:test python-version=3.11
  env: .uv-matrix/py3.11-1a2b3c4d
```

A job whose `when` expression is false is skipped rather than silently dropped.
The summary always reports how many jobs were skipped, so a `when` exclusion is
never invisible; pass `-v` to also name each skipped job:

```text
2 jobs skipped (when)
All jobs passed.
```

By default the first failing job stops the run; later jobs do not run. When one
or more jobs fail, `run` lists them and exits `1`:

```text
Failed jobs:
  - test:test python-version=3.11: exit 1
```

A job whose `continue-on-error` is true does not stop the run — the remaining
jobs still run — but its failure still counts: the run exits `1` and the job
appears under `Failed jobs:`. `continue-on-error` only changes whether the run
stops, never whether a failure is reflected in the exit code. See
{doc}`configuration`.

`--matrix NAME`
: Run only the matrix with this name. An unknown matrix name is an error.

`--task NAME`
: Run only the task with this name. An unknown task name is an error.

`--filter KEY=VALUE`
: Run only jobs whose matrix cell has `KEY` = `VALUE`. Repeatable: repeating a
  key ORs its values, different keys AND. An unknown key or value is an error.
  The value after `=` may be empty (`--filter KEY=`) to select cells whose value
  is the empty string. Combines with `--matrix` and `--task` (all must match).

`--max-jobs N`
: Run up to `N` jobs concurrently, overriding `max-jobs`. See {ref}`parallel`.

`--dry-run`
: Print each job's command but do not execute it.

`-- ARG ...`
: Arguments after `--` are exposed to templates as `{{ posargs }}` and applied
  to every selected job. See {ref}`posargs`.

The `continue-on-error` setting (see {doc}`configuration`) controls whether `run`
stops at the first failure or keeps going and reports all failures at the end.

(parallel)=

## Parallel execution

By default `run` executes jobs **sequentially**, inheriting the terminal's stdio
so each job's output streams live as it happens. This is the right default for
local runs where you want to watch a single job's output.

Pass `--max-jobs N` (or set `max-jobs` in `pyproject.toml`; see
{doc}`configuration`) to run up to `N` jobs at once:

```bash
uv-matrix run --max-jobs 4
```

`--max-jobs` overrides `max-jobs` for that invocation. A value of `1` (the
default) means sequential.

### Output in parallel mode

Streaming several jobs' output to the same terminal at once would interleave
them unreadably, so in parallel mode each job's output is **captured** (stdout
and stderr combined) and printed as a single block once the job finishes, under
the same `==> matrix:task …` banner used in sequential mode. Each block is
therefore attributable to exactly one job. Sequential mode never captures —
output is inherited and streams live.

### Shared environments are serialized

Jobs that resolve to the same isolated environment (same `env_key` — identical
Python version, `groups`, `extras`, and `uv-args`) are run one at a time even
under `--max-jobs`, so their `uv sync` calls do not race on the same
`.uv-matrix/<key>/` directory. Jobs with different environments run fully
concurrently.

### Stopping in parallel mode

When a failing job stops the run (its `continue-on-error` is false, the default),
jobs that have **not yet started** are cancelled, while jobs **already running**
are allowed to finish and their output is still reported. The run then exits
non-zero, listing the failures it collected. A failing `continue-on-error` job
does not cancel anything — the run keeps going (but still exits non-zero).

### `list`

Lists the selectable jobs — matrix name, task, and matrix cell — so you can see
what to target on the command line. It only expands the matrices; it does
**not** evaluate `when`, templates, or expressions, and never sets up an
environment or runs anything. (That makes it safe to run against a config you do
not yet trust.)

```text
test:test python-version=3.11
test:test python-version=3.12
checks:lint python-version=3.13
checks:typecheck python-version=3.13
```

`task` (positional, optional)
: Show only the job(s) for this task name.

`--filter KEY=VALUE`
: Show only jobs whose matrix cell has `KEY` = `VALUE`. Repeatable: repeating a
  key ORs its values, different keys AND. An unknown key or value is an error.
  The value after `=` may be empty (`--filter KEY=`) to select cells whose value
  is the empty string.

Because nothing is evaluated, `list` shows every `(cell, task)` combination;
`when` filtering and the resolved command line appear only at `run` time.

## Common options

These options are accepted by both `run` and `list` (place them after the
subcommand, e.g. `uv-matrix run -v --no-color`):

`--config PATH`
: Read this `pyproject.toml` instead of discovering one.

`--project DIR`
: Use `DIR` as the project root (and `DIR/pyproject.toml` as the config, unless
  `--config` is also given). uv-matrix changes to this directory before running.

`-v`, `--verbose`
: Increase verbosity (repeatable). At `-v`, `run` also reports jobs skipped by a
  `when` condition.

`-q`, `--quiet`
: Decrease verbosity (repeatable). At `-q`, `run` prints only failures.

`--no-color`
: Disable colored output. Color is also disabled when the
  [`NO_COLOR`](https://no-color.org/) environment variable is set, or when
  output is not a terminal.

`uv-matrix --version` prints the version and exits.

## Shell features in `run`

A task's `run` is executed through a shell, so it can use shell features —
pipes, `&&`, redirects, `$VAR` expansion — and they all run in the job's
environment.

(environments)=

## Environments

Each job runs in its own isolated environment under `.uv-matrix/<key>/`
(analogous to tox's `.tox/<envname>/`), selected via the
`UV_PROJECT_ENVIRONMENT` variable. This keeps your project's `.venv` untouched
and stops one job's `groups`/`extras` from leaking into another. The directory
name is keyed by everything that determines the environment (Python version,
`groups`, `extras`, `uv-args`), so identical environments are reused across runs
and different ones never collide. Add `.uv-matrix/` to your `.gitignore`.

A task's own `env` value for `UV_PROJECT_ENVIRONMENT` overrides the default.
