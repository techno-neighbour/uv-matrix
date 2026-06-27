# uv-matrix

A matrix runner for [Astral uv](https://docs.astral.sh/uv/) projects. Not that
kind of UV matrix.

`uv-matrix` expands declarative matrices from `pyproject.toml` into jobs, runs
them, and reports the failures. It does not create environments,
install Python versions, or resolve dependencies — uv already does that. The
matrix axes are plain declarative arrays; tasks add logic only where needed, as
Jinja2 templates and Python expressions.

:::{warning}
Status: early development
:::

## Why uv-matrix instead of tox?

tox is a general-purpose test environment manager: it builds virtualenvs, installs dependencies, and discovers Python interpreters itself. `uv-matrix` does none of that. It delegates environment management to uv and only schedules the matrix of commands to run.


## Where to next

- {doc}`installation` — install uv-matrix into a uv project.
- {doc}`quickstart` — set up pytest and ruff matrices and run them.
- {doc}`usage` — the `run` and `list` commands and their flags.
- {doc}`configuration` — the full `[tool.uv-matrix]` reference.

```{toctree}
:hidden:
:maxdepth: 2

installation
quickstart
usage
configuration
changelog
```
