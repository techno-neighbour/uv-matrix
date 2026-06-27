"""Tests for the uv-matrix MVP: matrix expansion, evaluation, job resolution."""

import os

import pytest

from uv_matrix.config import (
    ConfigError,
    axis_values,
    expand_matrix,
    find_pyproject,
    iter_plan,
    load_config,
    matrix_axes,
    parse_filters,
    validate_axis_name,
)
from uv_matrix.evaluate import EvalError, build_context, eval_expr, render_string, render_template
from uv_matrix.runner import TaskError, _shell_command, resolve_job


def test_expand_matrix_cartesian_product():
    cells = expand_matrix({"python": ["3.11", "3.12"], "os": ["ubuntu", "macos"]})
    assert cells == [
        {"python": "3.11", "os": "ubuntu"},
        {"python": "3.11", "os": "macos"},
        {"python": "3.12", "os": "ubuntu"},
        {"python": "3.12", "os": "macos"},
    ]


def test_expand_matrix_no_axes_yields_single_empty_cell():
    assert expand_matrix({}) == [{}]


def test_expand_matrix_rejects_non_array_axis():
    with pytest.raises(ConfigError):
        expand_matrix({"python": "3.13"})


def test_iter_plan_expands_named_matrices():
    config = {
        "matrix": {
            "test": {"python": ["3.11", "3.12", "3.13"], "tasks": ["test"]},
            "checks": {"python": ["3.13"], "tasks": ["lint", "typecheck"]},
        }
    }
    plan = list(iter_plan(config))
    assert plan == [
        ("test", {"python": "3.11"}, "test"),
        ("test", {"python": "3.12"}, "test"),
        ("test", {"python": "3.13"}, "test"),
        ("checks", {"python": "3.13"}, "lint"),
        ("checks", {"python": "3.13"}, "typecheck"),
    ]


def test_iter_plan_matrix_without_axes():
    config = {"matrix": {"docs": {"tasks": ["build-docs"]}}}
    assert list(iter_plan(config)) == [("docs", {}, "build-docs")]


def test_iter_plan_requires_tasks():
    with pytest.raises(ConfigError):
        list(iter_plan({"matrix": {"test": {"python": ["3.13"]}}}))


def test_iter_plan_requires_a_matrix():
    with pytest.raises(ConfigError):
        list(iter_plan({}))


def test_validate_axis_name_accepts_ordinary_names():
    # ':' is allowed in axis names; only '=' and whitespace are forbidden.
    for name in ("python", "python-version", "django_version", "os.name", "py3.13", "ns:axis"):
        assert validate_axis_name(name) == name


def test_validate_axis_name_rejects_equals_whitespace_and_empty():
    for bad in ("py=thon", "py thon", "py\tthon", "py\nthon", ""):
        with pytest.raises(ConfigError):
            validate_axis_name(bad)


def test_matrix_axes_strips_tasks_and_validates():
    assert matrix_axes({"python": ["3.13"], "tasks": ["t"]}) == {"python": ["3.13"]}
    with pytest.raises(ConfigError):
        matrix_axes({"bad name": ["x"], "tasks": ["t"]})


def test_iter_plan_rejects_invalid_axis_name():
    config = {"matrix": {"m": {"bad=name": ["x"], "tasks": ["t"]}}}
    with pytest.raises(ConfigError):
        list(iter_plan(config))


def test_axis_values_rejects_invalid_axis_name():
    config = {"matrix": {"m": {"bad name": ["x"], "tasks": ["t"]}}}
    with pytest.raises(ConfigError):
        axis_values(config)


def test_render_string_uses_matrix_dict_lookup():
    ctx = {"matrix": {"python": "3.11"}, "vars": {}, "task": "test", "task_config": {}}
    assert render_string("py{{ matrix['python'] }}", ctx) == "py3.11"
    assert render_string("py{{ matrix['python'].replace('.', '') }}", ctx) == "py311"


def test_render_string_supports_jinja_filters():
    ctx = {"matrix": {"python": "3.11"}, "vars": {}, "task": "test", "task_config": {}}
    assert render_string("py{{ matrix['python'] | replace('.', '') }}", ctx) == "py311"


def test_render_string_passes_special_chars_verbatim():
    # A template with no placeholders renders byte-for-byte, including
    # backslashes and both quote styles. The old f-string-via-repr renderer
    # could mangle these (issue #13); Jinja2 leaves literal text untouched.
    ctx = {"matrix": {}}
    literal = r"""pytest -k "slow and fast" --path C:\tmp\x 'q' """
    assert render_string(literal, ctx) == literal


def test_render_string_single_braces_are_literal():
    # Single braces are ordinary text in Jinja2, so a shell brace expansion in a
    # `run` command survives. Under the f-string renderer this was evaluated as
    # an expression and raised.
    ctx = {"matrix": {}}
    assert render_string("echo {not-a-var}", ctx) == "echo {not-a-var}"
    assert render_string("cp a.{{ '{' }}b,c{{ '}' }}", ctx) == "cp a.{b,c}"


def test_render_template_recurses_into_lists():
    ctx = {"matrix": {"v": "1"}, "vars": {}, "task": "t", "task_config": {}}
    assert render_template(["a{{ matrix['v'] }}", "b"], ctx) == ["a1", "b"]


def test_eval_expr_bool_and_string():
    ctx = {"matrix": {"python": "3.13"}, "vars": {}, "task": "t", "task_config": {}}
    assert eval_expr(True, ctx) is True
    assert eval_expr("matrix['python'] == '3.13'", ctx) is True
    assert eval_expr("matrix['python'] == '3.11'", ctx) is False


def test_build_context_exposes_global_vars_and_matrix_name():
    config = {"vars": {"a": "1", "b": "2"}}
    task_config = {"vars": {"b": "override"}}
    ctx = build_context(config, "checks", {"python": "3.13"}, "lint", task_config)
    # `vars` are global only; a task's own `vars` are ignored.
    assert ctx["vars"] == {"a": "1", "b": "2"}
    assert ctx["task"] == "lint"
    assert ctx["matrix_name"] == "checks"
    assert ctx["matrix"] == {"python": "3.13"}


def test_resolve_job_builds_uv_command():
    tasks = {
        "test": {
            "python-version": "{{ matrix['python'] }}",
            "groups": ["test"],
            "run": "pytest -q",
        }
    }
    job = resolve_job({}, "test", {"python": "3.11"}, "test", tasks)
    assert job is not None
    assert job.command == [
        "uv",
        "run",
        "--python",
        "3.11",
        "--group",
        "test",
        *_shell_command("pytest -q"),
    ]
    assert job.label == "test:test python=3.11"


def test_shell_command_posix(monkeypatch):
    monkeypatch.setattr("uv_matrix.runner.sys.platform", "linux")
    assert _shell_command("pytest -q") == ["sh", "-c", "pytest -q"]


def test_shell_command_windows_uses_comspec(monkeypatch):
    monkeypatch.setattr("uv_matrix.runner.sys.platform", "win32")
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")
    assert _shell_command("pytest -q") == [r"C:\Windows\System32\cmd.exe", "/c", "pytest -q"]


def test_shell_command_windows_defaults_to_cmd(monkeypatch):
    monkeypatch.setattr("uv_matrix.runner.sys.platform", "win32")
    monkeypatch.delenv("COMSPEC", raising=False)
    assert _shell_command("pytest -q") == ["cmd.exe", "/c", "pytest -q"]


def test_resolve_job_passes_run_to_shell_verbatim():
    tasks = {"test": {"python-version": "3.12", "run": "pytest && echo ok | tee log"}}
    job = resolve_job({}, "m", {}, "test", tasks)
    assert job.command[-3:] == _shell_command("pytest && echo ok | tee log")


def test_resolve_job_inserts_uv_args():
    tasks = {
        "test": {
            "python-version": "3.12",
            "uv-args": ["--with", "{{ matrix['django'] }}"],
            "run": "pytest",
        }
    }
    job = resolve_job({}, "m", {"django": "Django>=4.2,<4.3"}, "test", tasks)
    assert job.command == [
        "uv",
        "run",
        "--python",
        "3.12",
        "--with",
        "Django>=4.2,<4.3",
        *_shell_command("pytest"),
    ]


def test_resolve_job_skips_empty_rendered_list_elements():
    tasks = {
        "test": {
            "python-version": "3.12",
            "groups": ["{{ matrix['django'] or '' }}", "test"],
            "extras": ["  ", "cli"],
            "uv-args": ["--with", "{{ matrix['extra'] or '' }}"],
            "run": "pytest",
        }
    }
    job = resolve_job({}, "m", {"django": "", "extra": ""}, "test", tasks)
    # The empty/whitespace-only elements are dropped instead of emitting
    # --group "", --extra "" or a stray --with "".
    assert job.command == [
        "uv",
        "run",
        "--python",
        "3.12",
        "--group",
        "test",
        "--extra",
        "cli",
        "--with",
        "sh",
        "-c",
        "pytest",
    ]


def test_resolve_job_strips_rendered_list_elements():
    tasks = {"test": {"python-version": "3.12", "groups": ["  test  "], "run": "pytest"}}
    job = resolve_job({}, "m", {}, "test", tasks)
    assert job.command == [
        "uv",
        "run",
        "--python",
        "3.12",
        "--group",
        "test",
        "sh",
        "-c",
        "pytest",
    ]


def test_build_context_posargs_default_empty():
    ctx = build_context({}, "m", {}, "t", {})
    assert ctx["posargs"] == ""


def test_build_context_posargs_shell_quoted():
    ctx = build_context({}, "m", {}, "t", {}, ["-k", "slow and fast", "-x"])
    assert ctx["posargs"] == "-k 'slow and fast' -x"


def test_build_context_exposes_environ_copy(monkeypatch):
    monkeypatch.setenv("UV_MATRIX_TEST_VAR", "hello")
    ctx = build_context({}, "m", {}, "t", {})
    assert ctx["environ"]["UV_MATRIX_TEST_VAR"] == "hello"
    # The context holds a copy, so mutating it does not touch os.environ.
    ctx["environ"]["UV_MATRIX_TEST_VAR"] = "changed"
    assert os.environ["UV_MATRIX_TEST_VAR"] == "hello"


def test_build_context_exposes_platform():
    import sys

    ctx = build_context({}, "m", {}, "t", {})
    assert ctx["platform"] == sys.platform


def test_when_can_gate_on_platform():
    import sys

    tasks = {"test": {"run": "pytest", "when": f"platform == {sys.platform!r}"}}
    assert resolve_job({}, "m", {}, "test", tasks) is not None
    tasks_off = {"test": {"run": "pytest", "when": "platform == 'nonexistent-os'"}}
    assert resolve_job({}, "m", {}, "test", tasks_off) is None


def test_resolve_job_expands_environ_into_run(monkeypatch):
    monkeypatch.setenv("UV_MATRIX_GREETING", "hi")
    tasks = {"test": {"python-version": "3.12", "run": "echo {{ environ['UV_MATRIX_GREETING'] }}"}}
    job = resolve_job({}, "m", {}, "test", tasks)
    assert job.command[-3:] == _shell_command("echo hi")


def test_resolve_job_loads_envfile_into_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("FOO=from_file\n# a comment\nBAR=base\n")
    tasks = {"test": {"run": "pytest", "envfile": ".env"}}
    job = resolve_job({}, "m", {}, "test", tasks)
    assert job.env["FOO"] == "from_file"
    assert job.env["BAR"] == "base"


def test_resolve_job_env_overrides_envfile(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("FOO=from_file\nBAR=base\n")
    tasks = {"test": {"run": "pytest", "envfile": ".env", "env": {"FOO": "from_env"}}}
    job = resolve_job({}, "m", {}, "test", tasks)
    assert job.env["FOO"] == "from_env"  # `env` wins over `envfile`
    assert job.env["BAR"] == "base"  # untouched key keeps the file value


def test_resolve_job_envfile_visible_in_run(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("GREETING=hi\n")
    tasks = {
        "test": {
            "python-version": "3.12",
            "run": "echo {{ environ['GREETING'] }}",
            "envfile": ".env",
        }
    }
    job = resolve_job({}, "m", {}, "test", tasks)
    assert job.command[-3:] == _shell_command("echo hi")


def test_resolve_job_env_override_visible_in_run(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("GREETING=hi\n")
    tasks = {
        "test": {
            "python-version": "3.12",
            "run": "echo {{ environ['GREETING'] }}",
            "envfile": ".env",
            "env": {"GREETING": "yo"},
        }
    }
    job = resolve_job({}, "m", {}, "test", tasks)
    # `run` reads the post-override value through environ.
    assert job.command[-3:] == _shell_command("echo yo")


def test_resolve_job_env_can_reference_envfile(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("BASE=root\n")
    tasks = {
        "test": {
            "run": "pytest",
            "envfile": ".env",
            "env": {"DERIVED": "{{ environ['BASE'] }}/sub"},
        }
    }
    job = resolve_job({}, "m", {}, "test", tasks)
    assert job.env["DERIVED"] == "root/sub"


def test_resolve_job_when_sees_envfile(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("UV_MATRIX_FROM_FILE", raising=False)
    (tmp_path / ".env").write_text("UV_MATRIX_FROM_FILE=1\n")
    tasks = {
        "test": {
            "run": "pytest",
            "envfile": ".env",
            "when": "environ.get('UV_MATRIX_FROM_FILE') == '1'",
        }
    }
    # `when` is evaluated after envfile/env settle, so it reads the file's value.
    assert resolve_job({}, "m", {}, "test", tasks) is not None


def test_resolve_job_envfile_list_later_overrides_earlier(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("FOO=first\nONLY_BASE=keep\n")
    (tmp_path / ".env.local").write_text("FOO=second\n")
    tasks = {"test": {"run": "pytest", "envfile": [".env", ".env.local"]}}
    job = resolve_job({}, "m", {}, "test", tasks)
    assert job.env["FOO"] == "second"  # later file wins
    assert job.env["ONLY_BASE"] == "keep"


def test_resolve_job_envfile_path_is_templated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env.prod").write_text("STAGE=prod\n")
    tasks = {"test": {"run": "pytest", "envfile": ".env.{{ matrix['stage'] }}"}}
    job = resolve_job({}, "m", {"stage": "prod"}, "test", tasks)
    assert job.env["STAGE"] == "prod"


def test_resolve_job_envfile_missing_raises(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    tasks = {"test": {"run": "pytest", "envfile": ".env.absent"}}
    with pytest.raises(TaskError, match="envfile"):
        resolve_job({}, "m", {}, "test", tasks)


def test_resolve_job_expands_posargs_into_run():
    tasks = {"test": {"python-version": "3.12", "run": "pytest {{ posargs }}"}}
    job = resolve_job({}, "m", {}, "test", tasks, ["-k", "slow"])
    assert job.command[-3:] == _shell_command("pytest -k slow")


def test_resolve_job_posargs_default_renders_empty():
    tasks = {"test": {"python-version": "3.12", "run": "pytest {{ posargs }}"}}
    job = resolve_job({}, "m", {}, "test", tasks)
    assert job.command[-3:] == _shell_command("pytest ")


def test_env_key_stable_and_distinct():
    base = {"python-version": "3.12", "groups": ["test"], "run": "pytest"}
    a = resolve_job({}, "m", {}, "t", {"t": base})
    b = resolve_job({}, "other", {}, "t", {"t": base})  # same env inputs -> same key
    c = resolve_job({}, "m", {}, "t", {"t": {**base, "groups": ["lint"]}})  # different groups
    d = resolve_job({}, "m", {}, "t", {"t": {**base, "python-version": "3.13"}})
    assert a.env_key == b.env_key
    assert a.env_key != c.env_key
    assert a.env_key != d.env_key
    assert a.env_key.startswith("py3.12-")


def test_parse_filters_groups_by_key():
    config = {
        "matrix": {
            "a": {"python": ["3.11", "3.12"], "os": ["ubuntu"], "tasks": ["t"]},
            "b": {"python": ["3.13"], "tasks": ["t"]},
        }
    }
    assert parse_filters(config, ["python=3.11", "python=3.13", "os=ubuntu"]) == {
        "python": {"3.11", "3.13"},
        "os": {"ubuntu"},
    }


def test_parse_filters_splits_on_first_equals():
    # The key/value split is on the first '=', so a value may itself contain '='.
    config = {"matrix": {"a": {"expr": ["a==b"], "tasks": ["t"]}}}
    assert parse_filters(config, ["expr=a==b"]) == {"expr": {"a==b"}}


def test_parse_filters_allows_colon_in_axis_name():
    # ':' is permitted in axis names; the filter key still splits on '='.
    config = {"matrix": {"a": {"ns:axis": ["v"], "tasks": ["t"]}}}
    assert parse_filters(config, ["ns:axis=v"]) == {"ns:axis": {"v"}}


def test_parse_filters_supports_empty_string_value():
    # `--filter flag=` selects the empty-string value when the axis defines one.
    config = {"matrix": {"a": {"flag": ["", "on"], "tasks": ["t"]}}}
    assert parse_filters(config, ["flag="]) == {"flag": {""}}


def test_parse_filters_empty_value_unknown_when_axis_lacks_it():
    config = {"matrix": {"a": {"flag": ["on"], "tasks": ["t"]}}}
    with pytest.raises(ConfigError):
        parse_filters(config, ["flag="])  # axis has no "" value


def test_parse_filters_rejects_unknown_key_value_and_format():
    config = {"matrix": {"a": {"python": ["3.11"], "tasks": ["t"]}}}
    with pytest.raises(ConfigError):
        parse_filters(config, ["nope=x"])  # unknown key
    with pytest.raises(ConfigError):
        parse_filters(config, ["python=9.9"])  # unknown value
    with pytest.raises(ConfigError):
        parse_filters(config, ["python"])  # no "="


def _write_project(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.uv-matrix.matrix.m]\npython = ['3.11', '3.12']\ntasks = ['t']\n"
        "[tool.uv-matrix.tasks.t]\npython-version = '3.11'\nrun = 'x'\n",
        encoding="utf-8",
    )


def test_main_filter_selects_cells(tmp_path, monkeypatch, capsys):
    from uv_matrix.cli import main

    _write_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["list", "--filter", "python=3.12"]) == 0
    out = capsys.readouterr().out
    assert "python=3.12" in out
    assert "python=3.11" not in out


def _write_skip_project(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.uv-matrix.matrix.m]\npython = ['3.11', '3.12', '3.13']\ntasks = ['t']\n"
        "[tool.uv-matrix.tasks.t]\npython-version = '{{ matrix[\"python\"] }}'\nrun = 'x'\n"
        "when = \"matrix['python'] == '3.13'\"\n",
        encoding="utf-8",
    )


def test_main_run_summary_counts_skipped_jobs(tmp_path, monkeypatch, capsys):
    from uv_matrix.cli import main

    _write_skip_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["run", "--dry-run"]) == 0
    out = capsys.readouterr().out
    # The runnable cell still runs; the summary reports the skip count so a
    # `when` exclusion is never invisible (issue #7).
    assert "==> m:t python=3.13" in out
    assert "2 jobs skipped (when)" in out


def test_main_run_lists_skipped_jobs_under_verbose(tmp_path, monkeypatch, capsys):
    from uv_matrix.cli import main

    _write_skip_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["run", "-v", "--dry-run"]) == 0
    out = capsys.readouterr().out
    # Under -v each excluded cell is named, not silently dropped.
    assert "-- skipped (when): m:t python=3.11" in out
    assert "-- skipped (when): m:t python=3.12" in out


def test_main_run_no_skip_note_without_skips(tmp_path, monkeypatch, capsys):
    from uv_matrix.cli import main

    _write_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["run", "--dry-run"]) == 0
    assert "skipped" not in capsys.readouterr().out


def test_split_posargs():
    from uv_matrix.cli import _split_posargs

    assert _split_posargs(["run", "test", "--", "-k", "slow"]) == (["run", "test"], ["-k", "slow"])
    assert _split_posargs(["run", "--", "-k", "slow"]) == (["run"], ["-k", "slow"])
    assert _split_posargs(["run", "test"]) == (["run", "test"], [])
    assert _split_posargs(["run", "--"]) == (["run"], [])


def test_main_passes_posargs_to_run(tmp_path, monkeypatch, capsys):
    from uv_matrix.cli import main

    (tmp_path / "pyproject.toml").write_text(
        "[tool.uv-matrix.matrix.m]\npython = ['3.11']\ntasks = ['t']\n"
        "[tool.uv-matrix.tasks.t]\npython-version = '3.11'\nrun = 'pytest {{ posargs }}'\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    assert main(["run", "--dry-run", "--", "-k", "slow"]) == 0
    assert "pytest -k slow" in capsys.readouterr().out


def _write_multi_project(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.uv-matrix.matrix.test]\npython-version = ['3.11', '3.12']\ntasks = ['test']\n"
        "[tool.uv-matrix.matrix.checks]\npython-version = ['3.13']\ntasks = ['lint', 'test']\n"
        "[tool.uv-matrix.tasks.test]\nrun = 'pytest'\n"
        "[tool.uv-matrix.tasks.lint]\nrun = 'ruff check .'\n",
        encoding="utf-8",
    )


def test_main_run_matrix_option_selects(tmp_path, monkeypatch, capsys):
    from uv_matrix.cli import main

    _write_multi_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["run", "--dry-run", "--matrix", "checks"]) == 0
    out = capsys.readouterr().out
    assert "checks:lint" in out
    assert "checks:test" in out
    assert "test:test" not in out


def test_main_run_task_option_selects(tmp_path, monkeypatch, capsys):
    from uv_matrix.cli import main

    _write_multi_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["run", "--dry-run", "--task", "lint"]) == 0
    out = capsys.readouterr().out
    assert "checks:lint" in out
    assert ":test" not in out


def test_main_run_matrix_and_task_options_combine(tmp_path, monkeypatch, capsys):
    from uv_matrix.cli import main

    _write_multi_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["run", "--dry-run", "--matrix", "checks", "--task", "test"]) == 0
    out = capsys.readouterr().out
    assert "checks:test python-version=3.13" in out
    assert "checks:lint" not in out
    assert "test:test" not in out


def test_main_run_filter_selects_cells(tmp_path, monkeypatch, capsys):
    from uv_matrix.cli import main

    _write_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["run", "--dry-run", "--filter", "python=3.12"]) == 0
    out = capsys.readouterr().out
    assert "python=3.12" in out
    assert "python=3.11" not in out


def test_main_run_filter_repeated_key_ors_values(tmp_path, monkeypatch, capsys):
    from uv_matrix.cli import main

    (tmp_path / "pyproject.toml").write_text(
        "[tool.uv-matrix.matrix.m]\npython = ['3.11', '3.12', '3.13']\ntasks = ['t']\n"
        "[tool.uv-matrix.tasks.t]\npython-version = '3.11'\nrun = 'x'\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    assert main(["run", "--dry-run", "--filter", "python=3.11", "--filter", "python=3.13"]) == 0
    out = capsys.readouterr().out
    assert "python=3.11" in out
    assert "python=3.13" in out
    assert "python=3.12" not in out


def test_main_run_filter_selects_empty_string_value(tmp_path, monkeypatch, capsys):
    from uv_matrix.cli import main

    (tmp_path / "pyproject.toml").write_text(
        "[tool.uv-matrix.matrix.m]\npython = ['3.11']\nextra = ['', 'web']\ntasks = ['t']\n"
        "[tool.uv-matrix.tasks.t]\npython-version = '3.11'\nrun = 'x'\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    # `--filter extra=` keeps the empty-string cell and drops extra=web.
    assert main(["run", "--dry-run", "--filter", "extra="]) == 0
    out = capsys.readouterr().out
    assert "extra=" in out
    assert "extra=web" not in out


def test_main_run_filter_unknown_value_errors(tmp_path, monkeypatch, capsys):
    from uv_matrix.cli import main

    _write_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["run", "--dry-run", "--filter", "python=9.9"]) == 1
    assert "unknown value" in capsys.readouterr().err


def test_main_run_unknown_matrix_errors(tmp_path, monkeypatch, capsys):
    from uv_matrix.cli import main

    _write_multi_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["run", "--dry-run", "--matrix", "nope"]) == 1
    assert "unknown matrix" in capsys.readouterr().err


def test_main_run_unknown_task_errors(tmp_path, monkeypatch, capsys):
    from uv_matrix.cli import main

    _write_multi_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["run", "--dry-run", "--task", "nope"]) == 1
    assert "unknown task" in capsys.readouterr().err


def test_main_filter_unknown_key_errors(tmp_path, monkeypatch, capsys):
    from uv_matrix.cli import main

    _write_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["list", "--filter", "bad=1"]) == 1
    assert "unknown filter key" in capsys.readouterr().err


def test_main_chdirs_to_project_root(tmp_path, monkeypatch, capsys):
    from uv_matrix.cli import main

    (tmp_path / "pyproject.toml").write_text(
        "[tool.uv-matrix.matrix.m]\npython = ['3.11']\ntasks = ['t']\n"
        "[tool.uv-matrix.tasks.t]\npython-version = '3.11'\nrun = 'x'\n",
        encoding="utf-8",
    )
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)

    assert main(["list"]) == 0
    from pathlib import Path

    assert Path.cwd().resolve() == tmp_path.resolve()
    assert "m:t python=3.11" in capsys.readouterr().out


def test_list_does_not_evaluate(capsys):
    import argparse
    from pathlib import Path

    from uv_matrix.cli import _cmd_list

    config = {
        "matrix": {"m": {"python": ["3.11"], "tasks": ["t"]}},
        # `when` and the template would raise if list evaluated them:
        "tasks": {"t": {"python-version": "{{ matrix['MISSING'] }}", "run": "x", "when": "1 / 0"}},
    }
    rc = _cmd_list(config, argparse.Namespace(task=None, filter=None), Path("."))
    assert rc == 0
    assert "m:t python=3.11" in capsys.readouterr().out


def test_resolve_job_inherits_python_version_from_matrix():
    # A task with no `python-version` inherits the matrix cell's reserved
    # `python-version` axis value.
    tasks = {"test": {"run": "pytest"}}
    job = resolve_job({}, "m", {"python-version": "3.12"}, "test", tasks)
    assert job.python_version == "3.12"
    assert job.command[:4] == ["uv", "run", "--python", "3.12"]


def test_resolve_job_task_python_version_overrides_matrix():
    # An explicit task `python-version` wins over the matrix axis value.
    tasks = {"test": {"python-version": "3.13", "run": "pytest"}}
    job = resolve_job({}, "m", {"python-version": "3.10"}, "test", tasks)
    assert job.python_version == "3.13"


def test_resolve_job_without_python_version_omits_python_flag():
    # With no python-version on the task or matrix axis, the job runs without
    # --python and uv uses its default interpreter.
    job = resolve_job({}, "m", {}, "t", {"t": {"run": "pytest"}})
    assert job.python_version is None
    assert "--python" not in job.command
    assert job.command == ["uv", "run", *_shell_command("pytest")]


def test_resolve_job_when_skips():
    tasks = {
        "lint": {
            "python-version": "3.13",
            "run": "ruff check .",
            "when": "matrix['python'] == '3.13'",
        }
    }
    assert resolve_job({}, "m", {"python": "3.11"}, "lint", tasks) is None
    assert resolve_job({}, "m", {"python": "3.13"}, "lint", tasks) is not None


def test_resolve_job_extras_and_continue_on_error():
    tasks = {
        "test": {
            "python-version": "3.12",
            "extras": ["cli"],
            "run": "pytest",
            "continue-on-error": "matrix.get('experimental', False)",
        }
    }
    job = resolve_job({}, "m", {"experimental": True}, "test", tasks)
    assert "--extra" in job.command and "cli" in job.command
    assert job.continue_on_error is True


def test_resolve_job_continue_on_error_global_default_and_override():
    config = {"continue-on-error": True}
    base = {"python-version": "3.12", "run": "x"}
    # No task-level value -> inherits the global [tool.uv-matrix] default.
    assert resolve_job(config, "m", {}, "t", {"t": base}).continue_on_error is True
    # A task-level value overrides the global default.
    overridden = {**base, "continue-on-error": False}
    assert resolve_job(config, "m", {}, "t", {"t": overridden}).continue_on_error is False
    # With no global and no task value, the default is false (stop on failure).
    assert resolve_job({}, "m", {}, "t", {"t": base}).continue_on_error is False


def _run_project(tmp_path, monkeypatch, capsys, *, exit_codes, continue_on_error="False"):
    """Run `uv-matrix run` over a one-axis matrix with stubbed subprocess results.

    `exit_codes` maps a python version to the exit code its job should return.
    """
    import subprocess

    from uv_matrix.cli import main

    versions = list(exit_codes)
    (tmp_path / "pyproject.toml").write_text(
        f"[tool.uv-matrix.matrix.m]\npython = {versions!r}\ntasks = ['t']\n"
        f"[tool.uv-matrix.tasks.t]\npython-version = \"{{{{ matrix['python'] }}}}\"\n"
        f"run = 'x'\ncontinue-on-error = {continue_on_error!r}\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    def fake_run(command, **kwargs):
        # `python-version` flows through to `uv run --python <ver>`.
        version = command[command.index("--python") + 1]
        return subprocess.CompletedProcess(command, exit_codes[version])

    monkeypatch.setattr(subprocess, "run", fake_run)
    rc = main(["run"])
    return rc, capsys.readouterr().out


def test_run_continue_on_error_runs_all_but_exits_nonzero(tmp_path, monkeypatch, capsys):
    # With continue-on-error true, every job runs even though some fail, and the
    # failures are still reflected in the exit code (no suppression).
    rc, out = _run_project(
        tmp_path,
        monkeypatch,
        capsys,
        exit_codes={"3.11": 0, "3.12": 1, "3.13": 2},
        continue_on_error="True",
    )
    assert rc == 1
    assert "Failed jobs:" in out
    assert "m:t python=3.12: exit 1" in out
    assert "m:t python=3.13: exit 2" in out
    assert "allowed" not in out


def test_run_stops_at_first_failure_by_default(tmp_path, monkeypatch, capsys):
    # Default continue-on-error is false: the first failing job stops the run, so
    # later jobs never run.
    rc, out = _run_project(
        tmp_path,
        monkeypatch,
        capsys,
        exit_codes={"3.11": 1, "3.12": 0},
    )
    assert rc == 1
    assert "==> m:t python=3.11" in out
    assert "==> m:t python=3.12" not in out  # not reached after the first failure
    assert "Failed jobs:" in out
    assert "m:t python=3.11: exit 1" in out


def test_run_summary_all_pass(tmp_path, monkeypatch, capsys):
    rc, out = _run_project(tmp_path, monkeypatch, capsys, exit_codes={"3.11": 0, "3.12": 0})
    assert rc == 0
    assert "All jobs passed." in out
    assert "Failed jobs:" not in out


def test_run_failures_all_counted(tmp_path, monkeypatch, capsys):
    # 3.11 continues on error, 3.12 does not; both failures count toward exit 1.
    rc, out = _run_project(
        tmp_path,
        monkeypatch,
        capsys,
        exit_codes={"3.11": 1, "3.12": 1},
        continue_on_error="matrix['python'] == '3.11'",
    )
    assert rc == 1
    assert "Failed jobs:" in out
    assert "m:t python=3.11: exit 1" in out
    assert "m:t python=3.12: exit 1" in out
    assert "All required jobs passed." not in out


def test_resolve_job_undefined_task():
    with pytest.raises(TaskError):
        resolve_job({}, "m", {}, "missing", {})


def test_resolve_job_missing_run():
    with pytest.raises(TaskError):
        resolve_job({}, "m", {}, "t", {"t": {"python-version": "3.12"}})


def test_render_string_reports_error():
    with pytest.raises(EvalError):
        render_string("{{ matrix['missing'] }}", {"matrix": {}})


def test_main_version_prints_and_exits(capsys):
    from uv_matrix import __version__
    from uv_matrix.cli import main

    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_main_config_flag_selects_explicit_file(tmp_path, monkeypatch, capsys):
    from uv_matrix.cli import main

    project = tmp_path / "elsewhere"
    project.mkdir()
    _write_project(project)
    # Run from an unrelated cwd to prove discovery is not used.
    other = tmp_path / "cwd"
    other.mkdir()
    monkeypatch.chdir(other)

    assert main(["list", "--config", str(project / "pyproject.toml")]) == 0
    assert "m:t python=3.11" in capsys.readouterr().out


def test_main_project_flag_uses_dir_pyproject(tmp_path, monkeypatch, capsys):
    from uv_matrix.cli import main

    project = tmp_path / "proj"
    project.mkdir()
    _write_project(project)
    monkeypatch.chdir(tmp_path)

    assert main(["list", "--project", str(project)]) == 0
    assert "m:t python=3.11" in capsys.readouterr().out


def test_main_config_missing_file_errors(tmp_path, monkeypatch, capsys):
    from uv_matrix.cli import main

    monkeypatch.chdir(tmp_path)
    assert main(["list", "--config", str(tmp_path / "nope.toml")]) == 1
    assert "not found" in capsys.readouterr().err


def test_main_no_color_disables_ansi(tmp_path, monkeypatch, capsys):
    from uv_matrix.cli import main

    _write_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["run", "--dry-run", "--no-color"]) == 0
    out = capsys.readouterr().out
    assert "\033[" not in out
    assert "All jobs passed." in out


def test_no_color_env_disables_ansi(monkeypatch):
    import argparse

    from uv_matrix.cli import _use_color

    monkeypatch.setenv("NO_COLOR", "1")
    assert _use_color(argparse.Namespace(no_color=False)) is False


def test_run_quiet_suppresses_progress(tmp_path, monkeypatch, capsys):
    from uv_matrix.cli import main

    _write_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["run", "--dry-run", "-q"]) == 0
    out = capsys.readouterr().out
    assert out == ""


def test_load_config_missing_table(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[project]\nname = 'x'\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(pyproject)


def test_find_pyproject_walks_up_like_uv(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    nested = tmp_path / "src" / "pkg"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    assert find_pyproject() == (tmp_path / "pyproject.toml").resolve()


def test_find_pyproject_missing_raises(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigError):
        find_pyproject(tmp_path / "pkg" / "sub")


def _simple_job(name="t", python_version="3.12", continue_on_error=False):
    task = {"python-version": python_version, "run": "x"}
    if continue_on_error:
        task["continue-on-error"] = True
    return resolve_job({}, "m", {}, name, {name: task})


def test_parallelism_arg_overrides_config():
    import argparse

    from uv_matrix import cli

    assert cli._parallelism({"max-jobs": 5}, argparse.Namespace(max_jobs=3)) == 3
    assert cli._parallelism({"max-jobs": 5}, argparse.Namespace(max_jobs=None)) == 5
    assert cli._parallelism({}, argparse.Namespace(max_jobs=None)) == 1
    # Values below 1 clamp to sequential.
    assert cli._parallelism({"max-jobs": 0}, argparse.Namespace(max_jobs=None)) == 1
    assert cli._parallelism({}, argparse.Namespace(max_jobs=-4)) == 1


def test_parallelism_rejects_non_integer():
    import argparse

    from uv_matrix import cli

    with pytest.raises(ConfigError):
        cli._parallelism({"max-jobs": "lots"}, argparse.Namespace(max_jobs=None))


def test_run_sequential_inherits_stdio(monkeypatch):
    """Sequential runs must not capture output — stdio stays inherited."""
    import subprocess
    from pathlib import Path

    from uv_matrix import cli

    seen = []

    def fake_run(cmd, env=None, cwd=None, **kwargs):
        seen.append(kwargs)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    failed = cli._run_sequential([_simple_job()], Path("."), style=cli._Style(False), verbosity=0)
    assert failed == []
    # No stdout/stderr capture kwargs were passed.
    assert seen == [{}]


def test_run_parallel_runs_all_and_captures(monkeypatch, capsys):
    import subprocess
    from pathlib import Path

    from uv_matrix import cli

    seen = []

    def fake_run(cmd, env=None, cwd=None, **kwargs):
        seen.append(kwargs)
        return subprocess.CompletedProcess(cmd, 0, stdout="hello\n")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    jobs = [_simple_job(python_version=v) for v in ("3.11", "3.12", "3.13")]
    failed = cli._run_parallel(jobs, Path("."), parallel=2, style=cli._Style(False), verbosity=0)
    assert failed == []
    assert len(seen) == 3
    # Output is captured (stderr folded into stdout) rather than inherited.
    assert all(kw.get("stdout") is subprocess.PIPE for kw in seen)
    assert all(kw.get("stderr") is subprocess.STDOUT for kw in seen)
    assert capsys.readouterr().out.count("hello") == 3


def test_run_parallel_continue_on_error_collects_all_failures(monkeypatch):
    # continue-on-error jobs do not stop the run, so every failure is collected.
    import subprocess
    from pathlib import Path

    from uv_matrix import cli

    def fake_run(cmd, env=None, cwd=None, **kwargs):
        return subprocess.CompletedProcess(cmd, 2, stdout="boom\n")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    jobs = [
        _simple_job(python_version=v, continue_on_error=True) for v in ("3.11", "3.12", "3.13")
    ]
    failed = cli._run_parallel(jobs, Path("."), parallel=3, style=cli._Style(False), verbosity=0)
    assert len(failed) == 3
    assert all(code == 2 for _, code in failed)


def test_run_parallel_continue_on_error_still_counts_as_failure(monkeypatch):
    # A continue-on-error job that fails still counts toward the exit code; it is
    # no longer suppressed as an "allowed" failure.
    import subprocess
    from pathlib import Path

    from uv_matrix import cli

    job = _simple_job(continue_on_error=True)

    def fake_run(cmd, env=None, cwd=None, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    failed = cli._run_parallel([job], Path("."), parallel=2, style=cli._Style(False), verbosity=0)
    assert len(failed) == 1


def test_main_run_parallel_executes(tmp_path, monkeypatch, capsys):
    import subprocess

    from uv_matrix import cli
    from uv_matrix.cli import main

    _write_project(tmp_path)
    monkeypatch.chdir(tmp_path)

    def fake_run(cmd, env=None, cwd=None, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="ran\n")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    assert main(["run", "--max-jobs", "2"]) == 0
    out = capsys.readouterr().out
    assert "All jobs passed." in out
    assert "ran" in out


def test_load_config_reads_table(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        "[tool.uv-matrix]\ncontinue-on-error = true\n[tool.uv-matrix.matrix.test]\npython = ['3.13']\ntasks = ['test']\n",
        encoding="utf-8",
    )
    config = load_config(pyproject)
    assert config["continue-on-error"] is True
    assert config["matrix"]["test"] == {"python": ["3.13"], "tasks": ["test"]}
