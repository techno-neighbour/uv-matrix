"""uv-matrix: a GitHub Actions-style matrix runner for uv-based projects."""

from importlib.metadata import PackageNotFoundError, version

from .cli import main

try:
    __version__ = version("uv-matrix")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0"

__all__ = ["__version__", "main"]
