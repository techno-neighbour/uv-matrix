# Quickstart

This walkthrough sets up `uv-matrix` in a fresh project, configures two common
checks — **pytest** across several Python versions and **ruff** for linting and
formatting — and runs them. It assumes you already have a working
[uv](https://docs.astral.sh/uv/) install; see {doc}`installation` if not.

## 1. Create a project

If you do not already have one, start a new uv project:

```bash
uv init quickstart
cd quickstart
```

This gives you a `pyproject.toml`, a `hello.py`, and a `.venv` managed by uv.

## 2. Add the tools

Add `uv-matrix`, `pytest`, and `ruff` to the `dev` dependency group:

```bash
uv add --dev uv-matrix pytest ruff
```

Add a trivial test so pytest has something to run. Create `test_hello.py`:

```python
def test_addition():
    assert 1 + 1 == 2
```

## 3. Configure the matrices

Add the following to `pyproject.toml`. It defines two matrices — `test` runs
pytest once per Python version, and `checks` runs ruff once — plus the tasks
they reference:

```toml
# Run the test suite across every supported Python version.
[tool.uv-matrix.matrix.test]
python-version = ["3.10", "3.11", "3.12", "3.13"]
tasks = ["pytest"]

# Lint and format checks run once, on a single interpreter.
[tool.uv-matrix.matrix.checks]
tasks = ["lint", "format"]

[tool.uv-matrix.tasks.pytest]
run = "pytest -q"

[tool.uv-matrix.tasks.lint]
run = "ruff check ."

[tool.uv-matrix.tasks.format]
run = "ruff format --check ."
```

A matrix says *what to run over* (here, a list of Python versions); a task says
*what command to run*. The reserved `python-version` axis is special: each value
is passed to uv as the interpreter to run the job on, so the `pytest` task needs
no template to span four versions. See {doc}`configuration` for the full
reference.

## 4. See the jobs

`list` expands the matrices into their jobs without running anything:

```bash
uv run uv-matrix list
```

```text
test:pytest    python-version=3.10
test:pytest    python-version=3.11
test:pytest    python-version=3.12
test:pytest    python-version=3.13
checks:lint
checks:format
```

Four pytest jobs (one per interpreter) plus the two single checks.

## 5. Run everything

```bash
uv run uv-matrix run
```

`uv-matrix` runs each job in its own isolated environment under
`.uv-matrix/<key>/`, leaving your project's `.venv` untouched. Each job prints
its banner and the environment it uses:

```text
==> test:pytest python-version=3.10
  env: .uv-matrix/py3.10-1a2b3c4d
...
All jobs passed.
```

Add `.uv-matrix/` to your `.gitignore` so the per-job environments are not
committed.

## Useful variations

Run a single matrix, a single task, or a single cell:

```bash
uv run uv-matrix run --matrix test            # only the pytest jobs
uv run uv-matrix run --task lint              # only ruff check
uv run uv-matrix run --filter python-version=3.12
```

Run the matrices in parallel, and pass extra arguments through to pytest:

```bash
uv run uv-matrix run --max-jobs 4             # up to 4 jobs at once
uv run uv-matrix run --task pytest -- -k addition   # forwarded as {{ posargs }}
```

## Where to next

- {doc}`usage` — every `run` and `list` flag in detail.
- {doc}`configuration` — the full `[tool.uv-matrix]` reference: axes, templates,
  `when` conditions, environments, and more.
