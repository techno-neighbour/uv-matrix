# Installation

`uv-matrix` requires Python 3.10 or newer and a working [uv](https://docs.astral.sh/uv/)
installation, since it runs every job in your uv-managed project environment.

## As a development dependency

The usual place for `uv-matrix` is the `dev` dependency group of the project
whose matrix it runs:

```bash
uv add --dev uv-matrix
```

You can then invoke it inside the project environment:

```bash
uv run uv-matrix list
```

## As a tool

To run it without adding it to a project, use uv's tool runner:

```bash
uvx uv-matrix list
```

## From source

To work on `uv-matrix` itself, clone the repository and sync its environment:

```bash
git clone https://github.com/atsuoishimoto/uv-matrix
cd uv-matrix
uv sync
uv run uv-matrix --help
```

The only runtime dependency is `tomli`, and only on Python 3.10, where it backs
the `tomllib` standard-library module added in 3.11.

## Verifying the install

```bash
uv run uv-matrix --help
```

If that prints the command help, the `uv-matrix` console script is on your path.
Next, head to {doc}`usage`.
