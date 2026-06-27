# uv-matrix

A tiny matrix runner for Python projects using [Astral uv](https://docs.astral.sh/uv/).


> **Status:** early development.

`uv-matrix` expands declarative matrices from `pyproject.toml` into jobs, runs them with `uv run`, and reports failures.

📖 **Documentation:** https://uv-matrix.readthedocs.io/

uv-matrix does not manage Python interpreters, virtual environments, or dependencies itself. All interpreter discovery and download, virtual environment creation, and dependency resolution come from uv. `uv-matrix` only decides which commands to run, with which matrix values, and in what order.

## Why uv-matrix?

Many Python projects need to run the same checks across several Python versions, dependency versions, or task variants.

`uv-matrix` is `pyproject.toml`-centered: matrices and reusable tasks live alongside the rest of your project configuration:

```toml
[tool.uv-matrix.matrix.test]
python-version = ["3.12", "3.13"]
tasks = ["run-test"]

[tool.uv-matrix.tasks.run-test]
run = "pytest"
```

It is intentionally smaller than tox. tox manages test environments; `uv-matrix` delegates environment management to uv and focuses only on scheduling matrix jobs.

## How is this different from tox?

tox is powerful and mature, but matrix-style configuration can become hard to read when combinations are encoded into environment names and factors.

uv-matrix keeps the matrix explicit: Python versions, dependency variants, and task variants are written as plain axes in `pyproject.toml`.


## Installation

`uv-matrix` needs Python 3.10+ and a working uv install.

```bash
uv add --dev uv-matrix
uv run uv-matrix --help
```

Or run it directly:

```bash
uvx uv-matrix --help
```

## Configuration

Configuration lives under `[tool.uv-matrix]` in `pyproject.toml`.

```toml
[project]
name = "matrix-test"
version = "0.1.0"
requires-python = ">=3.12"

# Optional dependencies (extras) selectable per job via a task's `extras`.
[project.optional-dependencies]
django = [
    "django>=6.0.6",
]
flask = [
    "flask>=3.1.3",
]

# Dependency groups selectable per job via a task's `groups`.
[dependency-groups]
dev = [
    "ruff>=0.15.20",
]
doc = [
    "sphinx>=9.1.0",
]

[tool.uv-matrix]
continue-on-error = false  # stop the run on the first failing job (the default)
max-jobs = 4               # run up to 4 jobs at once (1 = sequential)

# A matrix named "test": every key except `tasks` is an axis, and the axes are
# combined as a cartesian product (here 2 x 3 = 6 cells).
[tool.uv-matrix.matrix.test]
python-version = ["3.12", "3.13"]   # reserved axis: inherited as `uv run --python`
webui = ["", "django", "flask"]     # arbitrary axis: read in templates as {{ webui }}
tasks = ["test"]                    # run these tasks for every cell


# A second, independent matrix. With no extra axes it runs each task once.
[tool.uv-matrix.matrix.checks]
python-version = ["3.13"]
tasks = ["lint", "doc"]

# Task definitions are reusable across matrices. `run` is the command to execute.
[tool.uv-matrix.tasks.test]
run = "pytest {{ posargs }}"   # {{ posargs }} expands to args passed after `--`
extras = ["{{ webui }}"]   # adds `--extra <webui>`; the empty "" cell renders blank and is dropped
when = "webui != 'django' or platform != 'win32'"   # run unless it's the django cell on Windows; a false `when` skips the job

[tool.uv-matrix.tasks.lint]
run = "ruff check ."

[tool.uv-matrix.tasks.doc]
groups = ["doc"]      # adds `--group doc` to the uv run command
run = "make html"
cwd = "docs"          # run the command from this directory
```

A matrix defines the values to test. A task defines the command to run.

Task fields support Jinja2 templates such as `{{ webui }}` and `{{ posargs }}`; see the [template reference](https://uv-matrix.readthedocs.io/en/latest/configuration.html#templates) for available variables and rendering rules.

`when` is evaluated with Python's `eval` against uv-matrix-provided context, so treat configuration as trusted project code; see the [conditions reference](https://uv-matrix.readthedocs.io/en/latest/configuration.html#variables) for available variables and examples.

These templates and `when` expressions are evaluated only when you invoke `run` (the point at which jobs are actually built and executed). Commands that merely enumerate jobs, such as `list`, expand the matrix without rendering templates or evaluating `when`, so nothing from your config is executed.

The example above expands to:

```text
test:test    python-version=3.12 webui=""
test:test    python-version=3.12 webui="django"
test:test    python-version=3.12 webui="flask"
test:test    python-version=3.13 webui=""
test:test    python-version=3.13 webui="django"
test:test    python-version=3.13 webui="flask"
checks:lint  python-version=3.13
checks:doc   python-version=3.13
```

Inside a matrix, every key except `tasks` defines an axis. The special `python-version` axis is inherited by tasks that do not set their own Python version.

Commands are executed through `uv run`. Each job runs in its own isolated environment rather than the project's `.venv`. For example, the `test` task above runs roughly like this on Linux:

```bash
uv run --python 3.12 sh -c "pytest"
```

## Usage

```bash
uv-matrix run                          # run every job from every matrix
uv-matrix run --matrix test            # run one matrix
uv-matrix run --filter webui=django    # select jobs
uv-matrix run --task lint              # run one task wherever it appears
uv-matrix run --max-jobs 4             # run up to 4 jobs at once
uv-matrix run --dry-run                # print commands without running them
uv-matrix run --task test -- -k slow   # pass extra args as {{ posargs }}
uv-matrix list                         # list selectable jobs
```

By default, `uv-matrix` finds `pyproject.toml` by walking up from the current directory, then runs from the project root. Override this with `--config PATH` or `--project DIR`.

## License

MIT License. See [LICENSE](LICENSE) for details.
