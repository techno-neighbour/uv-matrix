# Security

`uv-matrix` executes commands and evaluates expressions from `pyproject.toml`.
The `run` field runs arbitrary commands; templates are rendered with Jinja2 and
`when` is evaluated as Python. It is a developer tool for trusted repositories, not a safe sandbox.

Specifically:

- Every task's `run` field is executed through a shell, so it can run any
  command the invoking user can run.
- `python-version`, `run`, `cwd`, `groups`, `extras`, `uv-args`, and `env` are
  Jinja2 templates, rendered with no restrictions. Jinja2 is not used as a
  sandbox here: templates have full attribute and method access to the values
  in scope.
- `when` and `continue-on-error` are evaluated as plain Python expressions.

There is no allow-list, sandbox, or restricted builtins. A malicious
`pyproject.toml` can execute arbitrary code as soon as `uv-matrix run` touches
it.

`list` is the exception: it only expands the matrices and evaluates nothing, so
it has no side effects and is safe to run against a config you have not vetted.
All evaluation and execution happen in `run`.

**Only run `uv-matrix` in repositories you trust.**
