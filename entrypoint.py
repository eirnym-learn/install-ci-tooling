#!/usr/bin/env python3
from collections.abc import Iterable
import argparse
import dataclasses
import functools
import logging
import os
import re
import shutil
import subprocess
import sys
import tomllib
from typing import Any

logger = logging.getLogger()

TOOL_NAME_PATTERN = re.compile(r"[a-zA-Z0-9](?:[a-zA-Z0-9._-]+[a-zA-Z0-9])?")
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
SOURCE_PATTERN = re.compile(r"[a-z]+")
KNOWN_LOG_LEVELS = ("error", "warn", "info", "debug")
RUST_INSTALL_METHODS = ("prefer-binstall", "binstall", "install")
PYTHON_INSTALL_METHODS = ("prefer-uv", "uv", "pip")


def parse_args():
    parser = argparse.ArgumentParser(description="Simple install for CI tooling")
    parser.add_argument(
        "--rust-install-method",
        help="Method to install Rust tools",
        choices=RUST_INSTALL_METHODS,
    )
    parser.add_argument(
        "--python-install-method",
        help="Method to install Python tools",
        choices=PYTHON_INSTALL_METHODS,
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


@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class ToolInfo:
    name: str
    version: str
    source: str
    locked: bool = False


def validate_item(
    *,
    value: Any,
    field: str,
    tool_name: str | None,
    ty: type | Iterable[type],
    ty_str: str,
    mandatory: bool,
    regex: re.Pattern[str] | None,
    regex_str: str | None,
):
    def error_msg(msg: str):
        if tool_name:
            logger.error(f"{tool_name}: {msg}")
        else:
            logger.error(msg)

    field = field.capitalize()

    if value is None:
        if mandatory:
            error_msg(f"{field} is mandatory")
            return False
        return True

    if not isinstance(value, ty):
        error_msg(f"{field} must be {ty_str}")
        return False

    if regex is not None:
        if regex.fullmatch(value) is None:
            error_msg(f"{field} doesn't match {regex_str}: {value!r}")
            return False

    return True


validate_details = functools.partial(
    validate_item,
    field="tool details",
    ty=dict,
    ty_str="dict",
    mandatory=True,
    regex=None,
    regex_str=None,
)


validate_tool_name = functools.partial(
    validate_item,
    field="tool name",
    tool_name=None,
    ty=str,
    ty_str="string",
    mandatory=True,
    regex=TOOL_NAME_PATTERN,
    regex_str="crates.io or Pypi tool name",
)

validate_version = functools.partial(
    validate_item,
    field="version",
    ty=str,
    ty_str="string",
    mandatory=True,
    regex=VERSION_PATTERN,
    regex_str="semver or PEP440 scheme",
)

validate_source = functools.partial(
    validate_item,
    field="source",
    ty=str,
    ty_str="string",
    mandatory=True,
    regex=SOURCE_PATTERN,
    regex_str="source name pattern",
)

validate_locked = functools.partial(
    validate_item,
    field="locked",
    ty=bool,
    ty_str="boolean",
    mandatory=False,
    regex=None,
    regex_str=None,
)


def read_tools(toml_file: str, section: str) -> Iterable[ToolInfo] | None:
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

    result: list[ToolInfo] = []

    for tool_name, value in tools.items():
        if not validate_tool_name(value=tool_name):
            return None

        if not validate_details(tool_name=tool_name, value=value):
            return None

        version = value.get("version")
        source = value.get("source")
        locked = value.get("locked")

        if (
            validate_version(tool_name=tool_name, value=version)
            and validate_source(tool_name=tool_name, value=source)
            and validate_locked(tool_name=tool_name, value=locked)
        ):
            result.append(
                ToolInfo(name=tool_name, version=version, source=source, locked=locked)
            )
        else:
            return None

    return result


def check_tool_installed(
    name: str,
    version: str,
    force_install: bool,
    installed_tools: dict[str, str],
) -> bool:
    """Check if tool has been installed.

    Returns `True` if installed and not need to be reinstalled (by `force_install` flag).
    """
    installed_version = installed_tools.get(name)

    if not installed_version:
        logger.info(f"{name} not found, installing...")
        return False

    if installed_version == version:
        logger.info(f"{name} already installed with {version}")
        return False

    msg = (
        "{tool_name} version mismatch "
        f"(found: {installed_version}, expected: {version})."
    )
    if force_install:
        msg += " Reinstalling..."

    logger.warning(msg)
    return force_install


def run_install_tool(
    *,
    versioned_tool: str,
    dry_run: bool,
    prepared_command: Iterable[str],
    additional_args: Iterable[str],
):
    command = list(prepared_command)
    command.extend(additional_args)
    command.append(versioned_tool)

    logger.info(f"Installing {versioned_tool}")

    if dry_run:
        level = logging.WARN
    else:
        level = logging.DEBUG

    if logger.isEnabledFor(level):
        logger.log(level, f"Running install command: {' '.join(command)!r}")

    if not dry_run:
        subprocess.run(command, check=True, text=True)

    logger.info(f"Successfully installed {versioned_tool}")
    return True


def prepare_rust_install_command(
    *,
    force_install: bool,
    install_method: str,
) -> tuple[str, ...] | None:
    use_cargo_binstall: bool
    match install_method:
        case "prefer-binstall":
            use_cargo_binstall = bool(shutil.which("cargo-binstall"))
        case "binstall":
            use_cargo_binstall = True
        case "install":
            use_cargo_binstall = False
        case _:
            logger.error(f"Unknown installation method: {install_method}")
            return None

    if use_cargo_binstall:
        command = ["cargo", "binstall", "--no-confirm"]
    else:
        command = ["cargo", "install"]

    if force_install:
        command.append("--force")

    return tuple(command)


def prepare_python_install_command(
    *,
    force_install: bool,
    install_method: str,
) -> tuple[str, ...] | None:
    """Install Python package.

    TODO: Forced installation is not yet supported, but checked.
    """
    global warning_python_force_install_met
    use_python_uv: bool
    match install_method:
        case "prefer-uv":
            use_python_uv = bool(shutil.which("uv"))
        case "uv":
            use_python_uv = True
        case "pip":
            use_python_uv = False
        case _:
            logger.error(f"Unknown installation method: {install_method}")
            return None

    if use_python_uv:
        command = ["uv", "pip", "install"]
    else:
        command = ["pip", "install"]

    if force_install and not warning_python_force_install_met:
        logger.warning("Force install flag is not yet supported for Python packages")
        warning_python_force_install_met = True

    return tuple(command)


def list_installed_rust_tools() -> Iterable[tuple[str, str]]:
    cargo_home = os.environ.get("CARGO_HOME")

    if not cargo_home:
        cargo_home = os.path.expanduser("~/.cargo")

    crates_toml = os.path.join(cargo_home, ".crates.toml")

    if not os.path.exists(crates_toml):
        # fresh installation
        return ()

    with open(crates_toml) as f:
        data = tomllib.loads(f.read())

    data = data.get("v1")

    if not data:
        return ()

    result = []
    for key in data.keys():
        split = key.split(" ", 2)
        if len(split) < 2:
            logger.warning(f"Unable to parse {crates_toml}")
            return ()
        (crate, version, _) = split
        result.append((crate, version))

    return result


def list_installed_python_packages(
    install_method: str, dry_run: bool
) -> Iterable[tuple[str, str]]:
    """List installed python packages"""
    # currently it's unimplemented
    use_python_uv: bool
    match install_method:
        case "prefer-uv":
            if shutil.which("uv"):
                use_python_uv = True
            else:
                use_python_uv = False
        case "uv":
            use_python_uv = True
        case "pip":
            use_python_uv = False
        case _:
            logger.error(f"Unknown installation method: {install_method}")
            return ()

    if use_python_uv:
        command = ["uv", "pip", "list", "--format=freeze", "-q"]
    else:
        command = ["pip", "list", "--format=freeze"]

    if dry_run:
        level = logging.WARN
    else:
        level = logging.DEBUG

    if logger.isEnabledFor(level):
        logger.log(level, f"List python packages using command: {' '.join(command)!r}")

    if dry_run:
        return ()

    cmd_result = subprocess.run(command, check=True, text=True, stdout=subprocess.PIPE)

    result = []

    for line in cmd_result.stdout.splitlines():
        split = line.split("==")
        if len(split) != 2:
            logger.warning("Unable to parse python installed packages")
            return ()
        result.append(split)
    return result


warning_python_force_install_met = False


def main() -> bool:
    args = parse_args()

    setup_logging(args.log_level)

    logger.info(f"Installing cargo tools from {args.toml_file}/{args.section}")
    installed_rust_tools = dict(list_installed_rust_tools())
    installed_python_packages = dict(
        list_installed_python_packages(args.python_install_method, args.dry_run)
    )

    if logger.isEnabledFor(logging.DEBUG):
        for crate, version in installed_rust_tools.items():
            logger.debug(f"Installed rust crate {crate} {version}")

        for package, version in installed_python_packages.items():
            logger.debug(f"Installed python package {package} {version}")

    tools = read_tools(args.toml_file, args.section)
    if tools is None:
        return False

    prepared_command_rust = prepare_rust_install_command(
        force_install=args.force_install,
        install_method=args.rust_install_method,
    )
    prepared_command_python = prepare_python_install_command(
        force_install=args.force_install,
        install_method=args.python_install_method,
    )

    for tool in tools:
        installed_tools: dict[str, str]
        additional_args: list[str] = []
        match tool.source:
            case "crate":
                versioned_tool = f"{tool.name}@{tool.version}"
                installed_tools = installed_rust_tools
                prepared_command = prepared_command_rust
                if tool.locked:
                    additional_args.append("--locked")
            case "pypi":
                versioned_tool = f"{tool.name}=={tool.version}"
                installed_tools = installed_rust_tools
                prepared_command = prepared_command_python
                if tool.locked:
                    logger.info(
                        f"{tool.name}: Locked install is not supported for Python install"
                    )
            case _:
                logger.warning(f"{tool.name}: Source is not supported")
                continue

        if check_tool_installed(
            tool.name, tool.version, args.force_install, installed_tools
        ):
            continue  # Do nothing at this point

        try:
            run_install_tool(
                versioned_tool=versioned_tool,
                dry_run=args.dry_run,
                prepared_command=prepared_command,
                additional_args=additional_args,
            )
        except subprocess.CalledProcessError:
            return False

    return True


if __name__ == "__main__":
    if not main():
        sys.exit(1)
