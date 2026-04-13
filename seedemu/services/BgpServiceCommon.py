from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Callable, Dict, Iterable, Optional

from seedemu.core import Node, Router


BIRD_CONFIG_PATH = PurePosixPath("/etc/bird/bird.conf")
BIRD_CONTROL_SOCKET_PATH = PurePosixPath("/run/bird/bird.ctl")

CONTROL_INCLUDE_PATH = PurePosixPath("/etc/bird/bgp_control.conf")
CONTROL_STATE_PATH = PurePosixPath("/etc/bird/bgp_control_state.json")
CONTROL_SCRIPT_PATH = PurePosixPath("/usr/local/bin/bgp-control-service.py")
CONTROL_LOG_PATH = PurePosixPath("/var/log/bgp-control-service.log")

OBSERVATION_STATE_PATH = PurePosixPath("/etc/bird/bgp_observation_state.json")
OBSERVATION_LOG_PATH = PurePosixPath("/var/log/bgp-observation-service.log")
OBSERVATION_BINARY_PATH = PurePosixPath("/usr/local/bin/birdwatcher")

EXPOSURE_LOCAL = "local"
EXPOSURE_INTERNAL = "internal"
EXPOSURE_HOST = "host"
EXPOSURE_MODES = {EXPOSURE_LOCAL, EXPOSURE_INTERNAL, EXPOSURE_HOST}


@dataclass(frozen=True)
class ExposureConfig:
    mode: str = EXPOSURE_LOCAL
    host_port: Optional[int] = None
    protocol: str = "tcp"

    def bind_host(self) -> str:
        return "127.0.0.1" if self.mode == EXPOSURE_LOCAL else "0.0.0.0"

    def normalized_host_port(self, node_port: int) -> Optional[int]:
        if self.mode != EXPOSURE_HOST:
            return None
        return node_port if self.host_port is None else self.host_port


@dataclass(frozen=True)
class RouterAttachment:
    router: Router

    def key(self) -> tuple[int, str]:
        return (self.router.getAsn(), self.router.getName())


@dataclass(frozen=True)
class EndpointMetadata:
    asn: int
    router: str
    node_port: int
    exposure: ExposureConfig
    internal_address: Optional[str]

    def to_dict(self, *, scheme: str = "http") -> Dict[str, Any]:
        host_port = self.exposure.normalized_host_port(self.node_port)
        local_endpoint = f"{scheme}://127.0.0.1:{self.node_port}"
        internal_endpoint = (
            f"{scheme}://{self.internal_address}:{self.node_port}"
            if self.internal_address and self.exposure.mode != EXPOSURE_LOCAL
            else None
        )
        host_endpoint = (
            f"{scheme}://localhost:{host_port}"
            if host_port is not None
            else None
        )
        endpoints = {
            "local": local_endpoint,
            "internal": internal_endpoint,
            "host": host_endpoint,
        }
        return {
            "asn": self.asn,
            "router": self.router,
            "node_port": self.node_port,
            "protocol": self.exposure.protocol,
            "exposure_mode": self.exposure.mode,
            "bind_host": self.exposure.bind_host(),
            "internal_address": self.internal_address,
            "host_port": host_port,
            "endpoint": endpoints[self.exposure.mode],
            "endpoints": endpoints,
        }


def normalize_exposure(
    mode: str = EXPOSURE_LOCAL,
    *,
    host_port: Optional[int] = None,
    protocol: str = "tcp",
) -> ExposureConfig:
    assert mode in EXPOSURE_MODES, f"unsupported exposure mode: {mode}"
    assert protocol == "tcp", f"unsupported exposure protocol: {protocol}"
    if mode != EXPOSURE_HOST:
        assert host_port is None, "host_port is only valid for host exposure"
    elif host_port is not None:
        assert host_port > 0, "host_port must be positive"
    return ExposureConfig(mode=mode, host_port=host_port, protocol=protocol)


def apply_exposure(node: Node, exposure: ExposureConfig, node_port: int) -> ExposureConfig:
    host_port = exposure.normalized_host_port(node_port)
    if host_port is not None:
        node.addPortForwarding(host_port, node_port, exposure.protocol)
    return ExposureConfig(
        mode=exposure.mode,
        host_port=host_port,
        protocol=exposure.protocol,
    )


def resolve_router(target: Any, router_name: Optional[str] = None) -> RouterAttachment:
    """Resolve a router explicitly and never infer a default router."""

    if isinstance(target, Router):
        assert router_name is None or router_name == target.getName(), (
            "router_name must be omitted or match the provided Router instance"
        )
        return RouterAttachment(router=target)

    assert router_name, "router_name must be explicitly provided"
    router_names = set(target.getRouters())
    assert router_name in router_names, (
        f"router '{router_name}' not found in AS{target.getAsn()}; "
        f"available routers: {sorted(router_names)}"
    )

    router = target.getRouter(router_name)
    assert isinstance(router, Router), f"AS{target.getAsn()}/{router_name} is not a Router"
    return RouterAttachment(router=router)


def build_endpoint_metadata(
    router: Router,
    node_port: int,
    exposure: ExposureConfig,
    *,
    scheme: str = "http",
) -> Dict[str, Any]:
    return EndpointMetadata(
        asn=router.getAsn(),
        router=router.getName(),
        node_port=node_port,
        exposure=exposure,
        internal_address=router.getLoopbackAddress(),
    ).to_dict(scheme=scheme)


def path_str(path: PurePosixPath | str) -> str:
    return str(path)


def shell_quote(value: PurePosixPath | str) -> str:
    return shlex.quote(path_str(value))


def ensure_text_endswith_newline(content: str) -> str:
    return content if content.endswith("\n") else f"{content}\n"


def get_node_file_content(node: Node, path: PurePosixPath | str) -> str:
    return node.getFile(path_str(path)).get()[1]


def set_node_text_file(node: Node, path: PurePosixPath | str, content: str) -> Node:
    node.setFile(path_str(path), ensure_text_endswith_newline(content))
    return node


def set_node_json_file(node: Node, path: PurePosixPath | str, payload: Any) -> Node:
    return set_node_text_file(node, path, json.dumps(payload, indent=2, sort_keys=True))


def ensure_directory_start_command(node: Node, *paths: PurePosixPath | str) -> Node:
    for path in paths:
        node.appendStartCommand(f"mkdir -p {shell_quote(path)}")
    return node


def patch_node_file(
    node: Node,
    path: PurePosixPath | str,
    patcher: Callable[[str], str],
) -> bool:
    content = get_node_file_content(node, path)
    patched = patcher(content)
    if patched == content:
        return False
    node.setFile(path_str(path), ensure_text_endswith_newline(patched))
    return True


def patch_bird_config(node: Node, patcher: Callable[[str], str]) -> bool:
    return patch_node_file(node, BIRD_CONFIG_PATH, patcher)


def ensure_bird_include(
    node: Node,
    include_path: PurePosixPath | str,
    *,
    config_path: PurePosixPath | str = BIRD_CONFIG_PATH,
    marker: str = "Managed by seedemu/services",
) -> bool:
    """Inject an include into bird.conf if it is not already present."""

    include_line = f'include "{path_str(include_path)}";'
    content = get_node_file_content(node, config_path)
    if include_line in content:
        return False

    snippet = f"# {marker}\n{include_line}\n"
    define_matches = list(
        re.finditer(r"^define\s+[A-Z_]+\s*=\s*\([^)]+\);\s*$", content, flags=re.MULTILINE)
    )
    if define_matches:
        insert_at = define_matches[-1].end()
        patched = f"{content[:insert_at]}\n{snippet}{content[insert_at:]}"
    else:
        first_protocol = re.search(r"^protocol\s+\S+", content, flags=re.MULTILINE)
        if first_protocol:
            insert_at = first_protocol.start()
            patched = f"{content[:insert_at]}{snippet}\n{content[insert_at:]}"
        else:
            patched = ensure_text_endswith_newline(content) if content else ""
            if patched:
                patched += "\n"
            patched += snippet
    node.setFile(path_str(config_path), patched)
    return True
