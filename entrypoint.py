#!/usr/bin/env python3
from collections.abc import Iterator
import argparse
import logging
import os
import re
import subprocess
import sys
import tomllib
from typing import Any, Callable

logger = logging.getLogger(__name__)

# normal for Rust crates, quite restrictive for others
VERSION_RE = re.compile(r"\d+\.\d+\.\d+")
TOOL_NAME_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]+")
KNOWN_DATA_SOURCES = ("crate",)
KNOWN_LOG_LEVELS = ("error", "warn", "info", "debug")


def parse_args():
    parser = argparse.ArgumentParser(description="Simple install for CI tooling")
    parser.add_argument(
        "--cargo-binstall",
        help="Use `cargo binstall` to install Rust tools",
        action="store_true",
    )
    parser.add_argument(
        "--default-datasource",
        help="Default data source",
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

    if VERSION_RE.fullmatch(version) is None:
        logger.error(f"{tool_name}: Version doesn't match semver scheme: {version!r}")
        return False

    return True


def validate_tool_name(tool_name: str) -> bool:
    """Sanitizes tool name input.

    Returns `None` if tool name doesn't match the `TOOL_NAME_RE`.
    """
    tool_name = tool_name.strip()
    if TOOL_NAME_RE.fullmatch(tool_name) is None:
        logger.error(f"Tool name is unexpected: {tool_name!r}")
        return False

    return True


def validate_datasource(tool_name: str, datasource: Any) -> bool:
    """Sanitizes datasource input.

    Returns `None` if datasource is not a string.
    """
    if datasource is None:
        return True

    if not isinstance(datasource, str):
        logger.error(f"{tool_name}: Datasource is not a string")
        return False

    return True


def read_tools(
    toml_file: str, section: str, default_datasource: str | None
) -> list[tuple[str, Any, Any]] | None:
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

        if isinstance(value, str):  # value is version
            version = value
            datasource = default_datasource
        elif isinstance(value, dict):
            version = value.get("version", None)
            datasource = value.get("datasource", default_datasource)
        else:
            logger.error(f"{tool_name}: Value must be a string or dict")
            return None

        if not validate_version(tool_name, version):
            return None

        if not validate_datasource(tool_name, datasource):
            return None

        if datasource is None or datasource not in KNOWN_DATA_SOURCES:
            logger.warning(f"{tool_name}: Datasource is not supported")
            continue

        result.append((tool_name, version, datasource))

    return result


def install_tool_crate(
    crate_name: str,
    version: str,
    force: bool,
    dry_run: bool,
    cargo_binstall: bool,
    installed_crates: dict[Any, Any],
) -> None:
    """Install a crate"""
    if crate_name in installed_crates:
        installed_version = installed_crates[crate_name]

        if installed_version == version:
            logger.info(f"{crate_name} already installed at {version}")
            return
        else:
            logger.info(
                f"{crate_name} version mismatch "
                f"(found: {installed_version}, expected: {version}). reinstalling..."
            )
    else:
        logger.info(f"> {crate_name} not found, installing...")

    if cargo_binstall:
        cargo_tool = "binstall"
    else:
        cargo_tool = "install"

    command = ["cargo", cargo_tool]

    if cargo_binstall:
        command.append("--no-confirm")

    if force:
        command.append("--force")
    command.append(f"{crate_name}@{version}")
    logger.info(f"Installing {crate_name}@{version}")

    if dry_run:
        level = logging.WARN
    else:
        level = logging.DEBUG

    if logger.isEnabledFor(level):
        logger.log(level, f"Running install command: {' '.join(command)!r}")

    if not dry_run:
        result = subprocess.run(command, check=True, text=True)
        result.check_returncode()

    logger.info(f"Successfully installed {crate_name} version {version}")


DATA_SOURCE_PROCESSORS = {
    "crate": install_tool_crate,
}


def install_tool(
    tool_name: str,
    version: str,
    datasource: str,
    force: bool,
    dry_run: bool,
    cargo_binstall: bool,
    installed_crates: dict[Any, Any],
) -> None:
    install_function: Callable[..., None] | None = DATA_SOURCE_PROCESSORS.get(
        datasource
    )

    if install_function:
        install_function(
            tool_name, version, force, dry_run, cargo_binstall, installed_crates
        )


def parse_installed_cargo(crates_toml: str) -> Iterator[tuple[str, str]]:
    if not crates_toml:
        return

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


def crates_toml():
    cargo_home = os.environ.get("CARGO_HOME")
    if not cargo_home:
        cargo_home = os.path.expanduser("~/.cargo")
    return os.path.join(cargo_home, ".crates.toml")


def main() -> bool:
    args = parse_args()

    setup_logging(args.log_level)
    installed_crates = dict(parse_installed_cargo(crates_toml()))
    logger.debug(f"Installing cargo tools from {args.toml_file}/{args.section}")

    if logger.isEnabledFor(logging.DEBUG):
        logger.debug("Installed cargo packages:")
        for crate, version in installed_crates.items():
            print(f"{crate} {version}")
    tools = read_tools(args.toml_file, args.section, args.default_datasource)
    if tools is None:
        return False

    for tool_name, version, datasource in tools:
        try:
            install_tool(
                tool_name=tool_name,
                version=version,
                datasource=datasource,
                force=args.force_install,
                dry_run=args.dry_run,
                cargo_binstall=args.cargo_binstall,
                installed_crates=installed_crates,
            )
        except subprocess.CalledProcessError:
            return False

    return True


if __name__ == "__main__":
    if not main():
        sys.exit(1)
