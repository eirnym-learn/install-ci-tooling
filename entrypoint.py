#!/usr/bin/env python3
from collections.abc import Iterator
import argparse
import logging
import os
import re
import subprocess
import sys
import tomllib
from typing import Any, Protocol

logger = logging.getLogger(__name__)

# normal for Rust crates, quite restrictive for others
VERSION_PATTERN = re.compile(
    r"""
        ^
        v?           # v prefix (required for python)
        (?:\d+!)?    # epoch prefix (required for python)
        (?:0|[1-9]\d*)(?:\.(?:0|[1-9]\d*)){2} # release version x.y.z, no leading zeros for each segment
        ([-.][a-zA-Z]+(?:\.?\d+)?)? # loosen pre-release segments. E.g. -alpha.1, .post1, etc.
        (?:\+[a-zA-Z]+(?:\.\d+)?)?  # build/local info. E.g. +build.1
        $
        """,
    re.VERBOSE,
)
TOOL_NAME_PATTERN = re.compile(r"[a-zA-Z0-9](?:[a-zA-Z0-9._-]+[a-zA-Z0-9])?")
KNOWN_LOG_LEVELS = ("error", "warn", "info", "debug")


def parse_args():
    parser = argparse.ArgumentParser(description="Simple install for CI tooling")
    parser.add_argument(
        "--use-cargo-binstall",
        help="Use `cargo binstall` to install Rust tools",
        action="store_true",
    )
    parser.add_argument(
        "--use-python-uv",
        help="Use `uv` to install Python tools",
        action="store_true",
    )
    parser.add_argument("--force-install", help="Force reinstall", action="store_true")
    parser.add_argument(
        "--log-level",
        help="Set log level",
        choices=KNOWN_LOG_LEVELS,
        default="information",
    )
    parser.add_argument("-n", "--dry-run", help="Dry run", action="store_true")
    parser.add_argument(
        "toml_file",
        help="Path to the TOML file containing tool versions",
        metavar="tools-file",
    )

    parser.add_argument("section", help="Section to use")
    return parser.parse_args()


def setup_logging(log_level: str | None):
    match log_level:
        case "error":
            logging_level = logging.ERROR
        case "warn":
            logging_level = logging.WARNING
        case "info":
            logging_level = logging.INFO
        case "debug":
            logging_level = logging.DEBUG
        case _:
            logging_level = logging.INFO

    format = "%(asctime)s [%(levelname)s] %(message)s"
    logging.basicConfig(level=logging_level, format=format)


def validate_version(tool_name: str, version: Any) -> bool:
    """Sanitizes version input.

    Returns `None` if version isn't a string or doesn't match `VERSION_RE`
    """
    if version is None:
        logger.error(f"{tool_name}: Version is mandatory")
        return False

    if not isinstance(version, str):
        logger.error(f"{tool_name}: Version must be a string")
        return False

    if VERSION_PATTERN.fullmatch(version) is None:
        logger.error(f"{tool_name}: Version doesn't match semver scheme: {version!r}")
        return False

    return True


def validate_tool_name(tool_name: str) -> bool:
    """Sanitizes tool name input.

    Returns `None` if tool name doesn't match the `TOOL_NAME_RE`.
    """
    tool_name = tool_name.strip()
    if TOOL_NAME_PATTERN.fullmatch(tool_name) is None:
        logger.error(f"Tool name is unexpected: {tool_name!r}")
        return False

    return True


def validate_datasource(tool_name: str, datasource: Any) -> bool:
    """Sanitizes datasource input.

    Returns `None` if datasource is not a string.
    """
    if not isinstance(datasource, str):
        logger.error(f"{tool_name}: Datasource is not a string")
        return False

    return True


def read_tools(toml_file: str, section: str) -> list[tuple[str, Any, Any]] | None:
    """Reads given section from given tools file.

    Returns tuple of (tool name, version, datasource).

    default_datasource is used if datasource is not defined or invalid.
    """
    if not os.path.exists(toml_file):
        logger.error("Tools file doesn't exist")
        return None
    with open(toml_file) as f:
        data = tomllib.loads(f.read())

    tools: dict[str, Any] | None = data.get(section)

    if tools is None or not isinstance(tools, dict):
        logger.error(f"Tools file doesn't contain section requested: {section}")
        return None

    result: list[tuple[str, str, str]] = []

    for tool_name, value in tools.items():
        if not validate_tool_name(tool_name):
            return None

        if isinstance(value, dict):
            version = value.get("version")
            datasource = value.get("source")
        else:
            logger.error(f"{tool_name}: Value must be a dict")
            return None

        if not validate_version(tool_name, version):
            return None

        if not validate_datasource(tool_name, datasource):
            return None

        result.append((tool_name, version, datasource))

    return result


def check_tool_installed(
    tool_name: str,
    tool_version: str,
    force_install: bool,
    installed_tools: dict[str, str],
) -> bool:
    installed_version = installed_tools.get(tool_name)

    if installed_version:
        if installed_version == tool_version:
            logger.info(f"{tool_name} already installed with {tool_version}")
            return True
        else:
            if force_install:
                logger.warning(
                    f"{tool_name} version mismatch "
                    f"(found: {installed_version}, expected: {tool_version}). reinstalling..."
                )
            else:
                logger.warning(
                    f"{tool_name} version mismatch "
                    f"(found: {installed_version}, expected: {tool_version})."
                )
            return force_install
    else:
        logger.info(f"{tool_name} not found, installing...")

    return True


class PrepareCommandProtocol(Protocol):
    def __call__(
        self,
        *,
        tool_name: str,
        tool_version: str,
        force_install: bool,
        use_alternative_install: bool,
    ) -> tuple[str, list[str]]:
        """Prepare installation command for a specific source."""
        return ("tool@version", [])


def install_tool(
    *,
    tool_name: str,
    tool_version: str,
    force_install: bool,
    dry_run: bool,
    use_alternative_install: bool,
    installed_tools: dict[str, str],
    prepare_install_command: PrepareCommandProtocol,
):
    if not check_tool_installed(
        tool_name, tool_version, force_install, installed_tools
    ):
        return False

    versioned_tool, command = prepare_install_command(
        tool_name=tool_name,
        tool_version=tool_version,
        force_install=force_install,
        use_alternative_install=use_alternative_install,
    )

    logger.info(f"Installing {versioned_tool}")

    if dry_run:
        level = logging.WARN
    else:
        level = logging.DEBUG

    if logger.isEnabledFor(level):
        logger.log(level, f"Running install command: {' '.join(command)!r}")

    if not dry_run:
        result = subprocess.run(command, check=True, text=True)
        result.check_returncode()

    logger.info(f"Successfully installed {tool_name} version {tool_version}")
    return True


def prepare_rust_install_command(
    *,
    tool_name: str,
    tool_version: str,
    force_install: bool,
    use_alternative_install: bool,
) -> tuple[str, list[str]]:
    use_cargo_binstall = use_alternative_install

    if use_cargo_binstall:
        cargo_tool = "binstall"
    else:
        cargo_tool = "install"

    command = ["cargo", cargo_tool]

    if use_cargo_binstall:
        command.append("--no-confirm")

    if force_install:
        command.append("--force")

    versioned_tool = f"{tool_name}@{tool_version}"
    command.append(versioned_tool)

    return (versioned_tool, command)


def prepare_python_install_command(
    *,
    tool_name: str,
    tool_version: str,
    force_install: bool,
    use_alternative_install: bool,
) -> tuple[str, list[str]]:
    """Install Python package.

    TODO: Forced installation is not yet supported, but checked.
    """
    use_python_uv = use_alternative_install

    command = ["pip", "install"]
    if use_python_uv:
        command[0:0] = ["uv"]

    if force_install:
        logger.warning("Force install flag is not yet supported for Python packages")

    versioned_tool = f"{tool_name}=={tool_version}"
    command.append(versioned_tool)

    return (versioned_tool, command)


def list_installed_rust_tools() -> Iterator[tuple[str, str]]:
    cargo_home = os.environ.get("CARGO_HOME")

    if not cargo_home:
        cargo_home = os.path.expanduser("~/.cargo")

    crates_toml = os.path.join(cargo_home, ".crates.toml")

    if not os.path.exists(crates_toml):
        # fresh installation
        return

    with open(crates_toml) as f:
        data = tomllib.loads(f.read())

    data = data.get("v1")

    if not data:
        return

    for key in data.keys():
        (crate, version, _) = key.split(" ", 2)
        yield (crate, version)


def list_installed_python_packages(use_python_uv: bool) -> Iterator[tuple[str, str]]:
    """List installed python packages"""
    if False:
        yield ("", "")


def main() -> bool:
    args = parse_args()

    setup_logging(args.log_level)

    logger.error(f"{args!r}")
    logger.debug(f"Installing cargo tools from {args.toml_file}/{args.section}")
    installed_rust_tools = dict(list_installed_rust_tools())
    installed_python_packages = dict(list_installed_python_packages(args.use_python_uv))

    if logger.isEnabledFor(logging.DEBUG):
        for crate, version in installed_rust_tools.items():
            logger.info(f"Installed rust crate {crate} {version}")

        for package, version in installed_python_packages.items():
            logger.info(f"Installed python package {package} {version}")

    tools = read_tools(args.toml_file, args.section)
    if tools is None:
        return False

    for tool_name, tool_version, source in tools:
        installed_tools: dict[str, str]
        prepare_install_command: PrepareCommandProtocol
        use_alternative_install: bool

        match source:
            case "crate":
                installed_tools = installed_rust_tools
                prepare_install_command = prepare_rust_install_command
                use_alternative_install = args.use_cargo_binstall
            case "pypi":
                installed_tools = installed_rust_tools
                prepare_install_command = prepare_python_install_command
                use_alternative_install = args.use_python_uv
            case _:
                logger.warning(f"{tool_name}: Datasource is not supported")
                continue

        try:
            install_tool(
                tool_name=tool_name,
                tool_version=tool_version,
                force_install=args.force_install,
                dry_run=args.dry_run,
                use_alternative_install=use_alternative_install,
                installed_tools=installed_tools,
                prepare_install_command=prepare_install_command,
            )
        except subprocess.CalledProcessError:
            return False

    return True


if __name__ == "__main__":
    if not main():
        sys.exit(1)
