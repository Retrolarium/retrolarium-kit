"""Verified compiler payloads distributed with Retrolarium Kit.

The package owns immutable, hash-pinned payloads.  A requested suite is copied
to a user-writable cache before execution so wheel installs remain read-only
and Unix executable bits can be restored deterministically.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from importlib import resources
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import tempfile
from types import MappingProxyType
from typing import Any, Mapping


MANIFEST_NAME = "manifest.json"


class BuildToolchainError(RuntimeError):
    """A bundled compiler suite is unavailable or fails identity checks."""


@dataclass(frozen=True)
class ResolvedToolchain:
    """A verified, executable compiler suite materialized for one host."""

    suite: str
    release: str
    host: str
    root: Path
    commands: Mapping[str, Path]
    environment: Mapping[str, str]

    def command(self, name: str) -> Path:
        try:
            return self.commands[name]
        except KeyError as exc:
            raise BuildToolchainError(
                f"toolchain suite {self.suite!r} has no command {name!r}"
            ) from exc

    def process_environment(self, base: Mapping[str, str] | None = None) -> dict[str, str]:
        result = dict(os.environ if base is None else base)
        result.update(self.environment)
        return result

    def as_dict(self) -> dict[str, object]:
        return {
            "schema": "retrolarium.resolved-toolchain",
            "schema_version": 1,
            "suite": self.suite,
            "release": self.release,
            "host": self.host,
            "root": str(self.root),
            "commands": {name: str(path) for name, path in self.commands.items()},
            "environment": dict(self.environment),
        }


def _package_root():
    return resources.files("retrolarium_kit").joinpath("_toolchains")


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BuildToolchainError(f"{name} must be an object")
    return value


def _identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or any(
        character not in "abcdefghijklmnopqrstuvwxyz0123456789._-" for character in value
    ):
        raise BuildToolchainError(f"{name} is not a stable lowercase identifier")
    return value


def _relative_path(value: object, name: str) -> Path:
    if not isinstance(value, str) or not value:
        raise BuildToolchainError(f"{name} must be a non-empty relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise BuildToolchainError(f"{name} escapes the toolchain package: {value}")
    return path


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_build_toolchain_manifest() -> dict[str, Any]:
    try:
        document = json.loads(
            _package_root().joinpath(MANIFEST_NAME).read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise BuildToolchainError(f"cannot read bundled toolchain manifest: {exc}") from exc
    manifest = _mapping(document, "toolchain manifest")
    if manifest.get("schema") != "retrolarium.build-toolchains":
        raise BuildToolchainError("unsupported build-toolchain manifest schema")
    if manifest.get("schema_version") != 1:
        raise BuildToolchainError("unsupported build-toolchain manifest version")
    _identifier(manifest.get("release"), "toolchain release")
    suites = _mapping(manifest.get("suites"), "toolchain suites")
    components = _mapping(manifest.get("components"), "toolchain components")
    if not suites or not components:
        raise BuildToolchainError("toolchain manifest has no suites or components")
    for suite_id, raw_suite in suites.items():
        _identifier(suite_id, "suite id")
        suite = _mapping(raw_suite, f"suite {suite_id}")
        members = suite.get("components")
        if not isinstance(members, list) or not members:
            raise BuildToolchainError(f"suite {suite_id} has no components")
        for member in members:
            if member not in components:
                raise BuildToolchainError(
                    f"suite {suite_id} references unknown component {member!r}"
                )
    return manifest


def normalize_host(system: str | None = None, machine: str | None = None) -> str:
    """Map Python host names to stable manifest host identifiers."""
    system_name = (system or platform.system()).lower()
    machine_name = (machine or platform.machine()).lower()
    architecture = {
        "amd64": "x86_64",
        "x86_64": "x86_64",
        "x64": "x86_64",
        "aarch64": "aarch64",
        "arm64": "aarch64",
        "i386": "x86",
        "i686": "x86",
        "x86": "x86",
    }.get(machine_name, machine_name)
    operating_system = {
        "windows": "windows",
        "linux": "linux",
        "darwin": "macos",
        "freebsd": "freebsd",
    }.get(system_name, system_name)
    return f"{operating_system}-{architecture}"


def default_cache_root() -> Path:
    override = os.environ.get("RETROLARIUM_TOOLCHAIN_HOME")
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "Retrolarium" / "toolchains"
    base = os.environ.get("XDG_CACHE_HOME")
    if base:
        return Path(base) / "retrolarium" / "toolchains"
    return Path.home() / ".cache" / "retrolarium" / "toolchains"


def _host_component(
    manifest: dict[str, Any], component_id: str, host: str
) -> dict[str, Any] | None:
    component = _mapping(manifest["components"].get(component_id), component_id)
    hosts = _mapping(component.get("hosts"), f"component {component_id} hosts")
    value = hosts.get(host)
    return None if value is None else _mapping(value, f"component {component_id} host {host}")


def _file_records(host_component: dict[str, Any], name: str) -> list[dict[str, Any]]:
    values = host_component.get("files")
    if not isinstance(values, list) or not values:
        raise BuildToolchainError(f"{name} has no files")
    records = []
    for index, value in enumerate(values):
        record = _mapping(value, f"{name} files[{index}]")
        _relative_path(record.get("package_path"), "package_path")
        _relative_path(record.get("install_path"), "install_path")
        if not isinstance(record.get("size"), int) or record["size"] <= 0:
            raise BuildToolchainError(f"{name} files[{index}] has invalid size")
        sha256 = record.get("sha256")
        if (
            not isinstance(sha256, str)
            or len(sha256) != 64
            or any(character not in "0123456789abcdef" for character in sha256)
        ):
            raise BuildToolchainError(f"{name} files[{index}] has invalid SHA-256")
        records.append(record)
    return records


def _suite_definition(
    manifest: dict[str, Any], suite_id: str, host: str
) -> tuple[dict[str, Any], list[tuple[str, dict[str, Any]]]]:
    suites = _mapping(manifest["suites"], "toolchain suites")
    try:
        suite = _mapping(suites[suite_id], f"suite {suite_id}")
    except KeyError as exc:
        raise BuildToolchainError(f"unknown build-toolchain suite: {suite_id}") from exc
    selected: list[tuple[str, dict[str, Any]]] = []
    missing: list[str] = []
    for component_id in suite["components"]:
        host_component = _host_component(manifest, component_id, host)
        if host_component is None:
            missing.append(component_id)
        else:
            selected.append((component_id, host_component))
    if missing:
        raise BuildToolchainError(
            f"suite {suite_id!r} is unavailable for {host}; missing: {', '.join(missing)}"
        )
    return suite, selected


def _packaged_payload(record: dict[str, Any]) -> bytes:
    relative = _relative_path(record["package_path"], "package_path")
    payload = _package_root().joinpath(*relative.parts).read_bytes()
    if len(payload) != record["size"] or _sha256_bytes(payload) != record["sha256"]:
        raise BuildToolchainError(
            f"bundled payload fails identity check: {relative.as_posix()}"
        )
    return payload


def _verify_component_documents(
    component: dict[str, Any], component_id: str, host: str
) -> None:
    license_record = {
        "package_path": component.get("license_file"),
        "size": component.get("license_size"),
        "sha256": component.get("license_sha256"),
    }
    _file_records(
        {
            "files": [
                {"install_path": license_record["package_path"], **license_record}
            ]
        },
        f"component {component_id} license",
    )
    _packaged_payload(license_record)
    sources = component.get("corresponding_sources")
    if sources is not None:
        source_map = _mapping(
            sources, f"component {component_id} corresponding sources"
        )
        try:
            source = source_map[host]
        except KeyError as exc:
            raise BuildToolchainError(
                f"component {component_id} has no corresponding source for {host}"
            ) from exc
        source_record = _mapping(
            source, f"component {component_id} corresponding source for {host}"
        )
        _file_records(
            {
                "files": [
                    {
                        "install_path": source_record.get("package_path"),
                        **source_record,
                    }
                ]
            },
            f"component {component_id} corresponding source",
        )
        _packaged_payload(source_record)


def packaged_toolchain_status(host: str | None = None) -> dict[str, object]:
    """Verify package data and report which complete suites exist for a host."""
    manifest = load_build_toolchain_manifest()
    selected_host = host or normalize_host()
    suites: dict[str, object] = {}
    for suite_id in manifest["suites"]:
        try:
            _suite, components = _suite_definition(manifest, suite_id, selected_host)
            file_count = 0
            byte_count = 0
            for component_id, component in components:
                _verify_component_documents(
                    _mapping(manifest["components"][component_id], component_id),
                    component_id,
                    selected_host,
                )
                records = _file_records(component, f"{component_id}/{selected_host}")
                for record in records:
                    payload = _packaged_payload(record)
                    file_count += 1
                    byte_count += len(payload)
            suites[suite_id] = {
                "available": True,
                "components": [name for name, _component in components],
                "files": file_count,
                "bytes": byte_count,
            }
        except BuildToolchainError as exc:
            suites[suite_id] = {"available": False, "reason": str(exc)}
    return {
        "schema": "retrolarium.build-toolchain-status",
        "schema_version": 1,
        "release": manifest["release"],
        "host": selected_host,
        "suites": suites,
    }


def _installed_file_is_valid(path: Path, record: dict[str, Any]) -> bool:
    return (
        path.is_file()
        and path.stat().st_size == record["size"]
        and _sha256_file(path) == record["sha256"]
    )


def _installed_suite_is_valid(
    root: Path, components: list[tuple[str, dict[str, Any]]]
) -> bool:
    for component_id, component in components:
        for record in _file_records(component, component_id):
            relative = _relative_path(record["install_path"], "install_path")
            if not _installed_file_is_valid(root.joinpath(*relative.parts), record):
                return False
    return True


def materialize_build_toolchain(
    suite_id: str,
    *,
    cache_root: Path | None = None,
    host: str | None = None,
) -> ResolvedToolchain:
    """Copy and verify one complete suite into a user-writable cache."""
    manifest = load_build_toolchain_manifest()
    selected_host = host or normalize_host()
    _suite, components = _suite_definition(manifest, suite_id, selected_host)
    for component_id, _host_component_definition in components:
        _verify_component_documents(
            _mapping(manifest["components"][component_id], component_id),
            component_id,
            selected_host,
        )
    release = str(manifest["release"])
    base = (cache_root or default_cache_root()).expanduser().resolve()
    destination = base / release / selected_host / suite_id
    destination.parent.mkdir(parents=True, exist_ok=True)

    if not _installed_suite_is_valid(destination, components):
        staging = Path(tempfile.mkdtemp(prefix=f".{suite_id}-", dir=destination.parent))
        try:
            for component_id, component in components:
                for record in _file_records(component, component_id):
                    relative = _relative_path(record["install_path"], "install_path")
                    target = staging.joinpath(*relative.parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(_packaged_payload(record))
                    if record.get("executable") and os.name != "nt":
                        target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
            if not _installed_suite_is_valid(staging, components):
                raise BuildToolchainError("materialized toolchain failed identity checks")
            if destination.exists():
                shutil.rmtree(destination)
            os.replace(staging, destination)
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    commands: dict[str, Path] = {}
    environment: dict[str, str] = {}
    for component_id, component in components:
        raw_commands = _mapping(component.get("commands"), f"{component_id} commands")
        for name, value in raw_commands.items():
            command_name = _identifier(name, "command name")
            relative = _relative_path(value, f"command {command_name}")
            path = destination.joinpath(*relative.parts)
            if command_name in commands or not path.is_file():
                raise BuildToolchainError(f"invalid or duplicate command {command_name}")
            commands[command_name] = path
        raw_environment = component.get("environment", {})
        environment_entries = _mapping(raw_environment, f"{component_id} environment")
        for name, value in environment_entries.items():
            if not isinstance(name, str) or not name or not isinstance(value, str):
                raise BuildToolchainError(f"invalid environment entry in {component_id}")
            relative = _relative_path(value, f"environment {name}")
            environment[name] = str(destination.joinpath(*relative.parts))
    return ResolvedToolchain(
        suite=suite_id,
        release=release,
        host=selected_host,
        root=destination,
        commands=MappingProxyType(commands),
        environment=MappingProxyType(environment),
    )
