import pathlib
import subprocess
import pytest
import os
import re
import shlex
import shutil
import stat

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
locked_tool_rs = 'locked_tool_rs = {version = "5.6.4", source = "crate", locked = true}'
locked_tool_rs_ver = "locked_tool_rs@5.6.4"
locked_tool_py = 'locked_tool_py = {version = "0.9.1", source = "pypi", locked = true}'
locked_tool_py_ver = "locked_tool_py==0.9.1"
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
        locked_tool_rs,
        locked_tool_py,
        dev_tool_py,
        epoch_tool_py,
    )
)
all_install_tools_ver = (
    install_tool_rs_ver,
    install_tool_py_ver,
    locked_tool_rs_ver,
    locked_tool_py_ver,
    dev_tool_py_ver,
    epoch_tool_py_ver,
)
expected_locked = [locked_tool_rs_ver]
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
        f.write("\n")


def setup_bin_folder(
    bin_folder: pathlib.Path, has_cargo_binstall: bool, has_python_uv: bool
):
    def link_cmd(cmd: str):
        src = shutil.which(cmd)
        if src:
            os.symlink(src, bin_folder / cmd)

    bin_folder.mkdir()

    link_cmd("python")
    link_cmd("python3")

    if has_python_uv:
        pathlib.Path(bin_folder / "uv").touch()
        os.chmod(bin_folder / "uv", stat.S_IXUSR)
    if has_cargo_binstall:
        pathlib.Path(bin_folder / "cargo-binstall").touch()
        os.chmod(bin_folder / "cargo-binstall", stat.S_IXUSR)


def execute_command(
    cargo_home: pathlib.Path,
    tool_file: pathlib.Path,
    rust_install_method: str | None,
    python_install_method: str,
    force_install: bool,
    limit_bin_to: pathlib.Path | None,
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

    command_args.extend(("--rust-install-method", rust_install_method))

    command_args.extend(("--python-install-method", python_install_method))

    if force_install:
        command_args.append("--force-install")

    # it's better to insert this way to avoid ever-shifting argument index
    command_args.extend(args)

    command_args.extend((str(tool_file), SECTION))

    env = os.environ.copy()
    env["CARGO_HOME"] = str(cargo_home)
    print(f" cat {tool_file}; echo;")
    print(f" CARGO_HOME={env['CARGO_HOME']}", end=" ")
    if limit_bin_to is not None:
        env["PATH"] = (
            f"{limit_bin_to}:/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin"
        )
        print(f"PATH={env['PATH']}", end=" ")

    print(" ".join(command_args))
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


bool_values = (True, False)
# bool_values = (True,)

rust_install_opts = ("prefer-binstall", "binstall", "install")
python_install_opts = ("prefer-uv", "uv", "pip")


@pytest.mark.parametrize("rust_install_method", rust_install_opts)
@pytest.mark.parametrize("python_install_method", python_install_opts)
@pytest.mark.parametrize("has_crates_toml", bool_values)
@pytest.mark.parametrize("unknown_section", bool_values)
@pytest.mark.parametrize("has_cargo_binstall", bool_values)
@pytest.mark.parametrize("has_python_uv", bool_values)
@pytest.mark.parametrize("force_install", bool_values)
@pytest.mark.parametrize(
    "section_data,expected_tools,locked_tools,unsupported_tools",
    (
        (install_tool_rs, [install_tool_rs_ver], [], []),
        (install_tool_py, [install_tool_py_ver], [], []),
        (locked_tool_rs, [locked_tool_rs_ver], [locked_tool_rs_ver], []),
        (locked_tool_py, [locked_tool_py_ver], [], []),
        (dev_tool_py, [dev_tool_py_ver], [], []),
        (epoch_tool_py, [epoch_tool_py_ver], [], ()),
        (all_install_tools, all_install_tools_ver, [locked_tool_rs_ver], []),
        (unknown_tool, [], [], [unknown_tool_ver]),
        (
            all_install_tools_unknown,
            all_install_tools_ver,
            [locked_tool_rs_ver],
            [unknown_tool_ver],
        ),
    ),
)
def test_positive(
    section_data: str,
    expected_tools: list[str],
    locked_tools: list[str],
    unsupported_tools: list[str],
    has_crates_toml: bool,
    unknown_section: bool,
    rust_install_method: str,
    python_install_method: str,
    has_cargo_binstall: bool,
    has_python_uv: bool,
    force_install: bool,
    tmpdir: pathlib.Path,
):
    expected_tools = list(expected_tools)
    unsupported_tools = list(unsupported_tools)
    expected_tool_idx = 0
    unsupported_tool_idx = 0

    cargo_home = tmpdir / ".cargo"
    tool_file = tmpdir / "tools.toml"
    bin_folder = tmpdir / "bin"

    setup_bin_folder(bin_folder, has_cargo_binstall, has_python_uv)
    setup_cargo_home(cargo_home, has_crates_toml)
    setup_tools_toml(tool_file, section_data, unknown_section, raw=False)

    use_cargo_binstall = rust_install_method == "binstall" or (
        has_cargo_binstall and rust_install_method == "prefer-binstall"
    )
    use_python_uv = python_install_method == "uv" or (
        has_python_uv and python_install_method == "prefer-uv"
    )

    (code, stdout, stderr) = execute_command(
        cargo_home,
        tool_file,
        rust_install_method,
        python_install_method,
        force_install,
        limit_bin_to=bin_folder,
    )
    assert len(stdout) == 0
    assert code == 0

    warning_python_force_flag_met = False
    python_list_cmd_met = False

    for line in stderr.splitlines():
        assert "[ERROR]" not in line
        if "[DEBUG]" in line or "[INFO]" in line:
            continue

        warning_python_force_flag = False

        if " [WARNING] " in line:
            warning_python_force_flag = line.endswith(
                "[WARNING] Force install flag is not yet supported for Python packages"
            )

            if warning_python_force_flag:
                assert not warning_python_force_flag_met
                warning_python_force_flag_met = True

            if warning_python_force_flag:
                continue

            pip_list = re.sub(
                "^.* \\[WARNING] List python packages using command: '(.*)'$",
                "\\1",
                line,
            )
            install_tool = re.sub(
                "^.* \\[WARNING] Running install command: '(.*)'$", "\\1", line
            )
            unsupported = re.sub(
                "^.* \\[WARNING] ([^:]+): Source is not supported$", "\\1", line
            )

            if pip_list == line:
                pip_list = None

            if install_tool == line:
                install_tool = None

            if unsupported == line:
                unsupported = None

            assert pip_list or install_tool or unsupported, line
        else:
            pip_list = None
            install_tool = None
            unsupported = None

        if install_tool:
            cmd = shlex.split(install_tool)

            assert len(expected_tools) > expected_tool_idx, expected_tools
            expected_tool = expected_tools[expected_tool_idx]
            expected_tool_idx += 1

            match cmd[0]:
                case "cargo":
                    locked = expected_tool in locked_tools
                    check_cargo_install(
                        cmd, expected_tool, use_cargo_binstall, force_install, locked
                    )
                case "uv" | "pip":
                    check_python_install(
                        cmd,
                        expected_tool,
                        use_python_uv,
                        force_install,
                        warning_python_force_flag_met,
                    )
                case _:
                    assert False, f"{cmd[0]} is unsupported: {install_tool!r}"

        if pip_list:
            assert not python_list_cmd_met
            python_list_cmd_met = True
            cmd = shlex.split(pip_list)

            check_python_list(cmd, use_python_uv)

        if unsupported:
            assert len(unsupported_tools) > unsupported_tool_idx, unsupported_tools
            assert unsupported == unsupported_tools[unsupported_tool_idx], line
            unsupported_tool_idx += 1


def check_cargo_install(
    cmd: list[str],
    expected_tool: str,
    use_cargo_binstall: bool,
    force_install: bool,
    locked: bool,
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

    if locked:
        assert cmd[idx] == "--locked"
        idx += 1

    assert cmd[idx] == expected_tool

    assert len(cmd) == idx + 1


def check_python_install(
    cmd: list[str],
    expected_tool: str,
    use_python_uv: bool,
    force_install: bool,
    warning_python_force_flag_met: bool,
):
    idx = 0
    if use_python_uv:
        assert cmd[0] == "uv"
        idx += 1

    assert cmd[idx] == "pip"
    assert cmd[idx + 1] == "install"
    assert cmd[idx + 2] == expected_tool

    if force_install:
        assert warning_python_force_flag_met, "Warning hasn't been met"


def check_python_list(cmd: list[str], use_python_uv: bool):
    if use_python_uv:
        assert cmd == ["uv", "pip", "list", "--format=freeze", "-q"]
    else:
        assert cmd == ["pip", "list", "--format=freeze"]


@pytest.mark.parametrize(
    "section_data,expected_error",
    (
        # tool name is invalid
        (
            '-invalid_tool = "1.2.3"',
            "Tool name doesn't match crates.io or Pypi tool name: '-invalid_tool'",
        ),
        (
            '_invalid_tool = "1.2.3"',
            "Tool name doesn't match crates.io or Pypi tool name: '_invalid_tool'",
        ),
        (
            '"invalid_tool*" = "1.2.3"',
            "Tool name doesn't match crates.io or Pypi tool name: 'invalid_tool*'",
        ),
        # version name is unexpected or unexpected type
        ("invalid_tool = []", "invalid_tool: Tool details must be dict"),
        ("invalid_tool = true", "invalid_tool: Tool details must be dict"),
        ("invalid_tool = false", "invalid_tool: Tool details must be dict"),
        ("invalid_tool = 123", "invalid_tool: Tool details must be dict"),
        ('invalid_tool = "Wow"', "invalid_tool: Tool details must be dict"),
        ('invalid_tool = "1.2.3"', "invalid_tool: Tool details must be dict"),
        ('invalid_tool = "1.2.3.dev"', "invalid_tool: Tool details must be dict"),
        ('invalid_tool = "20!1.2.3"', "invalid_tool: Tool details must be dict"),
        # dversion is mandatory
        ("invalid_tool = {}", "invalid_tool: Version is mandatory"),
        ('invalid_tool = {"key" = "value"}', "invalid_tool: Version is mandatory"),
        ('invalid_tool = {"version" = {}}', "invalid_tool: Version must be string"),
        ('invalid_tool = {"version" = []}', "invalid_tool: Version must be string"),
        ('invalid_tool = {"version" = true}', "invalid_tool: Version must be string"),
        (
            'invalid_tool = {"version" = false}',
            "invalid_tool: Version must be string",
        ),
        ('invalid_tool = {"version" = 123}', "invalid_tool: Version must be string"),
        (
            'invalid_tool = {"version" = "Wow"}',
            "invalid_tool: Version doesn't match semver or PEP440 scheme: 'Wow'",
        ),
        ('invalid_tool = {"version" = "1.2.3"}', "invalid_tool: Source is mandatory"),
        (
            'invalid_tool = {"version" = "1.2.3", source = true}',
            "invalid_tool: Source must be string",
        ),
        (
            'invalid_tool = {"version" = "1.2.3", source = false}',
            "invalid_tool: Source must be string",
        ),
        (
            'invalid_tool = {"version" = "1.2.3", source = 123}',
            "invalid_tool: Source must be string",
        ),
        (
            'invalid_tool = {"version" = "1.2.3", source = []}',
            "invalid_tool: Source must be string",
        ),
        (
            'invalid_tool = {"version" = "1.2.3", source = {}}',
            "invalid_tool: Source must be string",
        ),
        (
            'invalid_tool = {"version" = "1.2.3", source = "Wow"}',
            "invalid_tool: Source doesn't match source name pattern: 'Wow'",
        ),
        (
            'invalid_tool = {"version" = "1.2.3", source = "1.2.3"}',
            "invalid_tool: Source doesn't match source name pattern: '1.2.3'",
        ),
        (
            'invalid_tool = {"version" = "1.2.3", source = "true", locked = {}}',
            "invalid_tool: Locked must be boolean",
        ),
        (
            'invalid_tool = {"version" = "1.2.3", source = "true", locked = []}',
            "invalid_tool: Locked must be boolean",
        ),
        (
            'invalid_tool = {"version" = "1.2.3", source = "true", locked = 123}',
            "invalid_tool: Locked must be boolean",
        ),
        (
            'invalid_tool = {"version" = "1.2.3", source = "true", locked = "123"}',
            "invalid_tool: Locked must be boolean",
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
        rust_install_method="prefer-binstall",
        python_install_method="prefer-uv",
        force_install=True,
        limit_bin_to=None,
    )
    assert len(stdout) == 0
    assert code != 0

    for line in stderr.splitlines():
        if "[DEBUG]" in line or "[INFO]" in line or "[WARNING]" in line:
            continue

        message = line.split("[ERROR] ", 1)

        assert len(message) == 2
        assert message[1] == expected_error
