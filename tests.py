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
unknown_tool_ds = 'unknown_tool = {"version" = "1.12.3", "datasource"= "unknown"}'

SECTION = "section"
CRATE_DATASOURCE = "crate"
SECTION_NEVER_INSTALLED = """
[section-never-installed]
never_tool = "1.12.3"
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
    cargo_binstall: bool,
    default_datasource: str | None,
    force: bool,
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

    if cargo_binstall:
        command_args.append("--cargo-binstall")

    if force:
        command_args.append("--force-install")

    if default_datasource is not None:
        command_args.append(f"--default-datasource={default_datasource}")

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
@pytest.mark.parametrize("cargo_binstall", (True, False))
@pytest.mark.parametrize("force", (True, False))
@pytest.mark.parametrize(
    "default_datasource,section_data,expected_tools,unsupported_tools",
    (
        ("crate", 'install_tool = "1.1.4"', ["install_tool@1.1.4"], []),
        ("crate", 'install_tool={"version" = "1.1.4"}', ["install_tool@1.1.4"], []),
        (
            "crate",
            'install_tool={"version" = "1.1.4", "datasource" = "crate"}',
            ["install_tool@1.1.4"],
            [],
        ),
        (
            "crate",
            'unknown_tool = {"version" = "1.12.3", "datasource" = "unknown"}',
            [],
            ["unknown_tool"],
        ),
        (
            "crate",
            f'install_tool = "1.1.4"\n{unknown_tool_ds}',
            ["install_tool@1.1.4"],
            ["unknown_tool"],
        ),
        (
            "crate",
            'install_tool = "1.1.4"\nother_tool = {"version" = "1.12.3"}',
            ["install_tool@1.1.4", "other_tool@1.12.3"],
            [],
        ),
        ("unknown", 'install_tool = "1.1.4"', [], ["install_tool"]),
        ("unknown", 'install_tool={"version" = "1.1.4"}', [], ["install_tool"]),
        (
            "unknown",
            'install_tool={"version" = "1.1.4", "datasource" = "crate"}',
            ["install_tool@1.1.4"],
            [],
        ),
        (
            "unknown",
            'unknown_tool = {"version" = "1.12.3", "datasource" = "unknown"}',
            [],
            ["unknown_tool"],
        ),
        (
            "unknown",
            f'install_tool = "1.1.4"\n{unknown_tool_ds}',
            [],
            ["install_tool", "unknown_tool"],
        ),
        (
            "unknown",
            'install_tool = "1.1.4"\nother_tool = {"version" = "1.12.3"}',
            [],
            ["install_tool", "other_tool"],
        ),
        (None, 'install_tool = "1.1.4"', [], ["install_tool"]),
        (None, 'install_tool={"version" = "1.1.4"}', [], ["install_tool"]),
        (
            None,
            'install_tool={"version" = "1.1.4", "datasource"= "crate"}',
            ["install_tool@1.1.4"],
            [],
        ),
        (
            None,
            unknown_tool_ds,
            [],
            ["unknown_tool"],
        ),
        (
            None,
            f'install_tool = "1.1.4"\n{unknown_tool_ds}',
            [],
            ["install_tool", "unknown_tool"],
        ),
        (
            None,
            'install_tool = "1.1.4"\nother_tool = {"version" = "1.12.3"}',
            [],
            ["install_tool", "other_tool"],
        ),
    ),
)
def test_positive(
    section_data: str,
    default_datasource: str | None,
    expected_tools: list[str],
    unsupported_tools: list[str],
    has_crates_toml: bool,
    unknown_section: bool,
    cargo_binstall: bool,
    force: bool,
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
        cargo_binstall,
        default_datasource,
        force,
    )
    assert len(stdout) == 0
    assert code == 0

    for line in stderr.splitlines():
        assert "[ERROR]" not in line
        if "[DEBUG]" in line or "[INFO]" in line:
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

            assert cmd[0] == "cargo"
            if cargo_binstall:
                assert cmd[1] == "binstall"
            else:
                assert cmd[1] == "install"

            idx = 2
            if cargo_binstall:
                assert cmd[idx] == "--no-confirm"
                idx += 1

            if force:
                assert cmd[idx] == "--force"
                idx += 1

            assert cmd[idx] == expected_tool

        if unsupported:
            assert len(unsupported_tools) > unsupported_tool_idx, unsupported_tools
            assert unsupported == unsupported_tools[unsupported_tool_idx]
            unsupported_tool_idx += 1


@pytest.mark.parametrize(
    "section_data,expected_error",
    (
        # tool name is invalid
        ('-invalid_tool = "1.2.3"', "Tool name is unexpected: '-invalid_tool'"),
        ('_invalid_tool = "1.2.3"', "Tool name is unexpected: '_invalid_tool'"),
        ('"invalid_tool*" = "1.2.3"', "Tool name is unexpected: 'invalid_tool*'"),
        # version name is unexpected or unexpected type
        ("invalid_tool = true", "invalid_tool: Value must be a string or dict"),
        ("invalid_tool = false", "invalid_tool: Value must be a string or dict"),
        ("invalid_tool = 123", "invalid_tool: Value must be a string or dict"),
        (
            'invalid_tool = "Wow"',
            "invalid_tool: Version doesn't match semver scheme: 'Wow'",
        ),
        # Semver suffix
        (
            'invalid_tool = "1.2.3.dev"',
            "invalid_tool: Version doesn't match semver scheme: '1.2.3.dev'",
        ),
        # Epoch prefix (Python specific)
        (
            'invalid_tool = "20!1.2.3"',
            "invalid_tool: Version doesn't match semver scheme: '20!1.2.3'",
        ),
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
        (
            'invalid_tool = {"version" = "1.2.3.dev"}',
            "invalid_tool: Version doesn't match semver scheme: '1.2.3.dev'",
        ),
        (
            'invalid_tool = {"version" = "20!1.2.3"}',
            "invalid_tool: Version doesn't match semver scheme: '20!1.2.3'",
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
        True,
        CRATE_DATASOURCE,
        True,
    )
    assert len(stdout) == 0
    assert code != 0

    for line in stderr.splitlines():
        if "[DEBUG]" in line or "[INFO]" in line or "[WARNING]" in line:
            continue

        message = line.split("[ERROR] ", 1)

        assert len(message) == 2
        assert message[1] == expected_error
