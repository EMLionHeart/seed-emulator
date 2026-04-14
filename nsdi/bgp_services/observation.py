"""BGP observation service."""

from __future__ import annotations

import re
from dataclasses import dataclass
from textwrap import dedent
from typing import Any, Dict, Iterable, List, Optional, Tuple

from seedemu.core import Emulator, Layer, Router

from .helpers import (
    BIRD_CONTROL_SOCKET_PATH,
    EXPOSURE_INTERNAL,
    OBSERVATION_BINARY_PATH,
    OBSERVATION_LOG_PATH,
    OBSERVATION_STATE_PATH,
    build_endpoint_metadata,
    ensure_directory_start_command,
    normalize_exposure,
    patch_bird_config,
    path_str,
    resolve_router,
    set_node_json_file,
    set_node_text_file,
    shell_quote,
)


DEFAULT_BIRDWATCHER_PORT = 29184
BIRDWATCHER_VERSION = "2.2.5"
BIRDWATCHER_SOURCE_DIR = "/tmp/birdwatcher-src"
BIRDWATCHER_CONFIG_PATH = "/etc/birdwatcher/birdwatcher.conf"


@dataclass(frozen=True)
class _ObservationTarget:
    router: Router
    port: int


def _ensure_birdwatcher_timeformat(bird_conf: str) -> str:
    required_lines = [
        "timeformat base         iso long;",
        "timeformat log          iso long;",
        "timeformat protocol     iso long;",
        "timeformat route        iso long;",
    ]
    missing = [line for line in required_lines if line not in bird_conf]
    if not missing:
        return bird_conf

    snippet = "\n".join(missing) + "\n"
    router_id = re.search(r"^router id\s+\S+;\s*$", bird_conf, flags=re.MULTILINE)
    if router_id:
        insert_at = router_id.end()
        return f"{bird_conf[:insert_at]}\n{snippet}{bird_conf[insert_at:]}"

    return snippet + "\n" + bird_conf


def _build_birdwatcher_config(port: int) -> str:
    return dedent(
        f"""\
        [server]
        allow_from = []
        allow_uncached = false
        modules_enabled = [
            "status",
            "protocols",
            "protocols_bgp",
            "protocols_short",
            "routes_filtered",
            "routes_noexport",
            "routes_peer",
            "routes_protocol",
        ]

        [status]
        reconfig_timestamp_match = "# Created: (.*)"
        filter_fields = []

        [ratelimit]
        enabled = false
        requests_per_minute = 60

        [bird]
        listen = "0.0.0.0:{port}"
        config = "/etc/bird/bird.conf"
        birdc = "birdc"
        ttl = 5
        dualstack = false

        [parser]
        filter_fields = []

        [cache]
        use_redis = false

        [housekeeping]
        interval = 5
        force_release_memory = true
        """
    )


class BGPObservationService(Layer):
    """Install birdwatcher on selected routers and record endpoints."""

    def __init__(self):
        super().__init__()
        self._targets: Dict[Tuple[int, str], _ObservationTarget] = {}
        self._endpoints: List[Dict[str, Any]] = []
        self.addDependency("Routing", False, False)
        self.addDependency("Ebgp", False, True)
        self.addDependency("Ibgp", False, True)

    def getName(self) -> str:
        return "BGPObservationService"

    def observeRouter(
        self,
        target: Any,
        router_name: Optional[str] = None,
        *,
        port: int = DEFAULT_BIRDWATCHER_PORT,
    ) -> "BGPObservationService":
        # Router-first is the only primary attachment model.
        # `as_obj` + `router_name` remains as a compatibility entry point only.
        attachment = resolve_router(target, router_name)
        self._targets[attachment.key()] = _ObservationTarget(
            router=attachment.router,
            port=port,
        )
        return self

    def observeRouters(self, attachments: Iterable[Dict[str, Any]]) -> "BGPObservationService":
        # Batch attachment follows the same rule: router-first is primary, and
        # `as_obj` is accepted only as a compatibility input shape.
        for attachment in attachments:
            self.observeRouter(
                attachment.get("router", attachment.get("as_obj")),
                attachment.get("router_name"),
                port=attachment.get("port", DEFAULT_BIRDWATCHER_PORT),
            )
        return self

    def getEndpoints(self) -> List[Dict[str, Any]]:
        return list(self._endpoints)

    def render(self, emulator: Emulator) -> None:
        del emulator
        self._endpoints = []

        for target in self._targets.values():
            self._install_target(target)

    def print(self, indent: int) -> str:
        out = " " * indent
        out += f"BGPObservationServiceLayer targets={len(self._targets)}\n"
        return out

    def _install_target(self, target: _ObservationTarget) -> None:
        router = target.router
        router.addSoftware("golang git")
        router.addBuildCommand(f"rm -rf {shell_quote(BIRDWATCHER_SOURCE_DIR)}")
        router.addBuildCommand(
            "git clone --branch {} --depth 1 https://github.com/alice-lg/birdwatcher.git {}".format(
                shell_quote(BIRDWATCHER_VERSION),
                shell_quote(BIRDWATCHER_SOURCE_DIR),
            )
        )
        router.addBuildCommand(
            "cd {} && GO111MODULE=on go mod download".format(
                shell_quote(BIRDWATCHER_SOURCE_DIR),
            )
        )
        router.addBuildCommand(
            "cd {} && GO111MODULE=on go build -o {} .".format(
                shell_quote(BIRDWATCHER_SOURCE_DIR),
                shell_quote(OBSERVATION_BINARY_PATH),
            )
        )
        router.addBuildCommand(f"rm -rf {shell_quote(BIRDWATCHER_SOURCE_DIR)}")

        ensure_directory_start_command(
            router,
            "/etc/birdwatcher",
            OBSERVATION_LOG_PATH.parent,
            OBSERVATION_STATE_PATH.parent,
        )

        patch_bird_config(router, _ensure_birdwatcher_timeformat)
        set_node_text_file(router, BIRDWATCHER_CONFIG_PATH, _build_birdwatcher_config(target.port))

        endpoint = build_endpoint_metadata(
            router,
            target.port,
            normalize_exposure(EXPOSURE_INTERNAL),
        )
        set_node_json_file(
            router,
            OBSERVATION_STATE_PATH,
            {
                **endpoint,
                "binary_path": path_str(OBSERVATION_BINARY_PATH),
                "config_path": BIRDWATCHER_CONFIG_PATH,
            },
        )

        router.appendStartCommand(
            f"while [ ! -x {shell_quote(OBSERVATION_BINARY_PATH)} ]; do echo \"bgp-observation: waiting for birdwatcher binary\"; sleep 1; done"
        )
        router.appendStartCommand(
            f"while [ ! -e {shell_quote(BIRD_CONTROL_SOCKET_PATH)} ]; do echo \"bgp-observation: waiting for bird\"; sleep 1; done"
        )
        router.appendStartCommand(
            f"{shell_quote(OBSERVATION_BINARY_PATH)} --config {shell_quote(BIRDWATCHER_CONFIG_PATH)} >{shell_quote(OBSERVATION_LOG_PATH)} 2>&1",
            True,
        )

        router.setAttribute("bgp_observation_port", target.port)
        router.setAttribute("bgp_observation_endpoint", endpoint["endpoint"])
        router.setAttribute("bgp_observation_endpoint_metadata", endpoint)
        self._endpoints.append(endpoint)
