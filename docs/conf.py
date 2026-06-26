# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import tomllib

# -- Path setup --------------------------------------------------------------
# Make the package importable so autodoc can introspect it without an install.
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

# -- Project information -----------------------------------------------------
_pyproject = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
_meta = _pyproject["project"]

project = "uv-matrix"
author = _meta["authors"][0]["name"]
copyright = f"{date.today().year}, {author}"
release = _meta["version"]
version = ".".join(release.split(".")[:2])

# -- General configuration ---------------------------------------------------
extensions = [
    "myst_parser",
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
]

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store", "README.md"]

# -- MyST (Markdown) configuration -------------------------------------------
myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "fieldlist",
    "linkify",
    "substitution",
    "tasklist",
]
myst_heading_anchors = 3

# -- Options for HTML output -------------------------------------------------
html_theme = "sphinx_rtd_theme"
html_static_path = ["_static"]
html_title = f"{project} {release}"
html_theme_options = {
    "navigation_depth": 3,
    "collapse_navigation": False,
    "style_external_links": True,
}

# "Edit on GitHub" link in the page header. On Read the Docs these values are
# injected automatically from the connected repository; set them here so the
# link also works for local builds.
html_context = {
    "display_github": True,
    "github_user": "atsuoishimoto",
    "github_repo": "uv-matrix",
    "github_version": "main",
    "conf_py_path": "/docs/",
}

# -- autodoc -----------------------------------------------------------------
autodoc_member_order = "bysource"
autodoc_typehints = "description"
autodoc_default_options = {
    "members": True,
    "undoc-members": True,
    "show-inheritance": True,
}

# -- napoleon ----------------------------------------------------------------
napoleon_google_docstring = True
napoleon_numpy_docstring = False

# -- intersphinx -------------------------------------------------------------
intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}
