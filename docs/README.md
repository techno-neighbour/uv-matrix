# Documentation

The `uv-matrix` documentation is built with [Sphinx](https://www.sphinx-doc.org/)
using [MyST](https://myst-parser.readthedocs.io/) (Markdown) and the
[Read the Docs theme](https://sphinx-rtd-theme.readthedocs.io/).

It is set up to build on [Read the Docs](https://readthedocs.org/): the
`.readthedocs.yaml` at the repository root installs the `docs` dependency group
with uv and runs `sphinx-build` against `docs/conf.py`.

## Building

The documentation dependencies live in the `docs` dependency group, so the
simplest way to build is through uv from the project root:

```bash
uv run --group docs sphinx-build -b html docs docs/_build/html
```

Then open `docs/_build/html/index.html`.

Alternatively, from inside this directory with the dependencies already
available:

```bash
make html        # build HTML into _build/html
make clean       # remove the build output
make linkcheck   # verify external links
```
