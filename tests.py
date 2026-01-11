import pathlib
import subprocess
import pytest
import os
import re

# Minimal .crates.toml to check against
_crates_index = "https://github.com/rust-lang/crates.io-index"
crates_toml = f"""
[v1]
"cargo-binstall 1.16.6 (registry+{_crates_index})" = ["cargo-binstall"]
"""
unknown_tool = 'unknown_tool = {version = "1.12.3", source = "unknown"}'
unknown_tool_ver = "unknown_tool"
install_tool_rs = 'install_tool_rs = {version = "1.1.4", source = "crate"}'
install_tool_rs_ver = "install_tool_rs@1.1.4"
install_tool_py = 'install_tool_py = {version = "2.8.9", source = "pypi"}'
install_tool_py_ver = "install_tool_py==2.8.9"
dev_tool_py = 'dev_tool_py = {"version" = "1.2.3.dev", source = "pypi"}'
dev_tool_py_ver = "dev_tool_py==1.2.3.dev"
epoch_tool_py = 'epoch_tool_py = {"version" = "1!1.2.3", source = "pypi"}'
epoch_tool_py_ver = "epoch_tool_py==1!1.2.3"
all_install_tools = "\n".join(
    (
        install_tool_rs,
        install_tool_py,
        dev_tool_py,
        epoch_tool_py,
    )
)
all_install_tools_ver = (
    install_tool_rs_ver,
    install_tool_py_ver,
    dev_tool_py_ver,
    epoch_tool_py_ver,
)

all_install_tools_unknown = "\n".join((all_install_tools, unknown_tool))


SECTION = "section"
SECTION_NEVER_INSTALLED = """
[section-never-installed]
never_tool = {version = "0.0.0", source = "crate"}
"""


def setup_cargo_home(cargo_home: pathlib.Path, has_crates_toml: bool = True):
    """Generate cargo home with only .crates_toml file if requested."""
    cargo_home.mkdir()
    if has_crates_toml:
        with open(cargo_home / ".crates.toml", "w") as f:
            f.write(crates_toml)


def setup_tools_toml(
    tool_file: pathlib.Path,
    section_data: str,
    unknown_section: bool = True,
    raw: bool = False,
):
    """Set up tools.toml in temporary file."""
    with open(tool_file, "w") as f:
        if raw:
            f.write(section_data)
        else:
            f.write(f"[{SECTION}]\n{section_data}")
            if unknown_section:
                f.write(SECTION_NEVER_INSTALLED)


def execute_command(
    cargo_home: pathlib.Path,
    tool_file: pathlib.Path,
    use_cargo_binstall: bool,
    use_python_uv: bool,
    force_install: bool,
    *args: str,
):
    """Execute command in debug mode.

    Returns (execution code, stdout, stderr).
    """
    command_args = [
        "./entrypoint.py",
        "--log-level=warn",
        "--dry-run",
    ]

    if use_cargo_binstall:
        command_args.append("--use-cargo-binstall")

    if use_python_uv:
        command_args.append("--use-python-uv")

    if force_install:
        command_args.append("--force-install")

    # it's better to insert this way to avoid ever-shifting argument index
    command_args.extend(args)

    command_args.extend((str(tool_file), SECTION))

    print(" ".join(command_args))

    env = os.environ.copy()
    env["CARGO_HOME"] = str(cargo_home)
    result = subprocess.run(
        command_args,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    return (
        result.returncode,
        result.stdout,
        result.stderr,
    )


@pytest.mark.parametrize("has_crates_toml", (True, False))
@pytest.mark.parametrize("unknown_section", (True, False))
@pytest.mark.parametrize("use_cargo_binstall", (True, False))
@pytest.mark.parametrize("use_python_uv", (True, False))
@pytest.mark.parametrize("force_install", (True, False))
@pytest.mark.parametrize(
    "section_data,expected_tools,unsupported_tools",
    (
        (install_tool_rs, [install_tool_rs_ver], []),
        (install_tool_py, [install_tool_py_ver], []),
        (dev_tool_py, [dev_tool_py_ver], []),
        (epoch_tool_py, [epoch_tool_py_ver], ()),
        (all_install_tools, all_install_tools_ver, []),
        (unknown_tool, [], [unknown_tool_ver]),
        (all_install_tools_unknown, all_install_tools_ver, [unknown_tool_ver]),
    ),
)
def test_positive(
    section_data: str,
    expected_tools: list[str],
    unsupported_tools: list[str],
    has_crates_toml: bool,
    unknown_section: bool,
    use_cargo_binstall: bool,
    use_python_uv: bool,
    force_install: bool,
    tmpdir: pathlib.Path,
):
    expected_tools = list(expected_tools)
    unsupported_tools = list(unsupported_tools)
    expected_tool_idx = 0
    unsupported_tool_idx = 0
    cargo_home = tmpdir / ".cargo"
    tool_file = tmpdir / "tools.toml"

    setup_cargo_home(cargo_home, has_crates_toml)
    setup_tools_toml(tool_file, section_data, unknown_section, raw=False)

    (code, stdout, stderr) = execute_command(
        cargo_home,
        tool_file,
        use_cargo_binstall,
        use_python_uv,
        force_install,
    )
    assert len(stdout) == 0
    assert code == 0

    warning_python_force_flag = False

    for line in stderr.splitlines():
        assert "[ERROR]" not in line
        if "[DEBUG]" in line or "[INFO]" in line:
            continue

        warning_python_force_flag = warning_python_force_flag or line.endswith(
            "[WARNING] Force install flag is not yet supported for Python packages"
        )

        if warning_python_force_flag:
            continue
        install_tool = re.sub(
            "^.* \\[WARNING] Running install command: '(.*)'$", "\\1", line
        )
        unsupported = re.sub(
            "^.* \\[WARNING] ([^:]+): Datasource is not supported$", "\\1", line
        )
        if install_tool == line:
            install_tool = None
        if unsupported == line:
            unsupported = None

        if install_tool:
            cmd = install_tool.split()

            assert len(expected_tools) > expected_tool_idx, expected_tools
            expected_tool = expected_tools[expected_tool_idx]
            expected_tool_idx += 1

            match cmd[0]:
                case "cargo":
                    check_cargo_install(
                        cmd, expected_tool, use_cargo_binstall, force_install
                    )
                case "uv" | "pip":
                    check_python_install(
                        cmd,
                        expected_tool,
                        use_python_uv,
                        force_install,
                        warning_python_force_flag,
                    )
                case _:
                    assert False, f"{cmd[0]} is unsupported: {install_tool!r}"

        if unsupported:
            assert len(unsupported_tools) > unsupported_tool_idx, unsupported_tools
            assert unsupported == unsupported_tools[unsupported_tool_idx], line
            unsupported_tool_idx += 1


def check_cargo_install(
    cmd: list[str], expected_tool: str, use_cargo_binstall: bool, force_install: bool
):
    """Checks tool installation using cargo"""
    assert cmd[0] == "cargo"
    if use_cargo_binstall:
        assert cmd[1] == "binstall"
    else:
        assert cmd[1] == "install"

    idx = 2
    if use_cargo_binstall:
        assert cmd[idx] == "--no-confirm"
        idx += 1

    if force_install:
        assert cmd[idx] == "--force"
        idx += 1

    assert cmd[idx] == expected_tool

    assert len(cmd) == idx + 1


def check_python_install(
    cmd: list[str],
    expected_tool: str,
    use_python_uv: bool,
    force_install: bool,
    warning_python_force_flag: bool,
):
    idx = 0
    if use_python_uv:
        assert cmd[0] == "uv"
        idx += 1

    assert cmd[idx] == "pip"
    assert cmd[idx + 1] == "install"
    assert cmd[idx + 2] == expected_tool

    if force_install:
        assert warning_python_force_flag, "Warning hasn't been met"


@pytest.mark.parametrize(
    "section_data,expected_error",
    (
        # tool name is invalid
        ('-invalid_tool = "1.2.3"', "Tool name is unexpected: '-invalid_tool'"),
        ('_invalid_tool = "1.2.3"', "Tool name is unexpected: '_invalid_tool'"),
        ('"invalid_tool*" = "1.2.3"', "Tool name is unexpected: 'invalid_tool*'"),
        # version name is unexpected or unexpected type
        ("invalid_tool = true", "invalid_tool: Value must be a dict"),
        ("invalid_tool = false", "invalid_tool: Value must be a dict"),
        ("invalid_tool = 123", "invalid_tool: Value must be a dict"),
        ('invalid_tool = "Wow"', "invalid_tool: Value must be a dict"),
        ('invalid_tool = "1.2.3"', "invalid_tool: Value must be a dict"),
        ('invalid_tool = "1.2.3.dev"', "invalid_tool: Value must be a dict"),
        ('invalid_tool = "20!1.2.3"', "invalid_tool: Value must be a dict"),
        # dversion is mandatory
        ("invalid_tool = {}", "invalid_tool: Version is mandatory"),
        ('invalid_tool = {"key" = "value"}', "invalid_tool: Version is mandatory"),
        ('invalid_tool = {"version" = {}}', "invalid_tool: Version must be a string"),
        ('invalid_tool = {"version" = true}', "invalid_tool: Version must be a string"),
        (
            'invalid_tool = {"version" = false}',
            "invalid_tool: Version must be a string",
        ),
        ('invalid_tool = {"version" = 123}', "invalid_tool: Version must be a string"),
        (
            'invalid_tool = {"version" = "Wow"}',
            "invalid_tool: Version doesn't match semver scheme: 'Wow'",
        ),
    ),
)
def test_negative(
    section_data: str,
    expected_error: str,
    tmp_path: pathlib.Path,
):
    cargo_home = tmp_path / ".cargo"
    tool_file = tmp_path / "tools.toml"
    setup_cargo_home(cargo_home, False)
    setup_tools_toml(tool_file, section_data, unknown_section=False, raw=False)

    (code, stdout, stderr) = execute_command(
        cargo_home,
        tool_file,
        use_cargo_binstall=True,
        use_python_uv=True,
        force_install=True,
    )
    assert len(stdout) == 0
    assert code != 0

    for line in stderr.splitlines():
        if "[DEBUG]" in line or "[INFO]" in line or "[WARNING]" in line:
            continue

        message = line.split("[ERROR] ", 1)

        assert len(message) == 2
        assert message[1] == expected_error
