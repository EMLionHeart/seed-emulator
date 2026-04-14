from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, List, Optional

import yaml
from seedemu.core import Emulator, Service


ALICE_CONFIG_FILENAME = "alice.conf"
ALICE_CONFIG_PATH = "../alice.conf"
ALICE_CONTAINER_CONFIG_PATH = "/etc/alice-lg/alice.conf"
ALICE_HOST_PORT = "8000:80"
DOCKER_HOST_ALIAS = "host.docker.internal:172.17.0.1"
ALICE_SERVICE_NAME = "alice_lg"


class AliceLGService(Service):
    def __init__(self):
        super().__init__()
        self._observation: Optional[Any] = None
        self._config_str: str = ""

    def getName(self) -> str:
        return "AliceLGService"

    def attachObservation(self, observation: Any) -> "AliceLGService":
        self._observation = observation
        return self

    def render(self, emulator: Emulator):
        del emulator

        if self._observation is None:
            raise RuntimeError("AliceLGService requires an attached observation service.")

        endpoints = self._observation.getEndpoints()
        if not endpoints:
            raise RuntimeError(
                "AliceLGService could not generate config: observation.getEndpoints() returned no endpoints."
            )

        lines = [
            "[server]",
            'listen = "0.0.0.0:8000"',
        ]

        for metadata in endpoints:
            source_id = f"as{metadata['asn']}_{metadata['router']}"
            display_name = f"AS{metadata['asn']} {metadata['router']}"
            port = metadata["node_port"]

            lines.extend(
                [
                    "",
                    f"[source.{source_id}]",
                    f'name = "{display_name}"',
                    "",
                    f"[source.{source_id}.birdwatcher]",
                    'type = "single_table"',
                    f'api = "http://host.docker.internal:{port}/"',
                ]
            )

        self._config_str = "\n".join(lines) + "\n"

    def get_output_callbacks(self) -> List[Callable]:
        def write_config(_compiler) -> None:
            if not self._config_str:
                raise RuntimeError("AliceLGService could not write config: render() has not generated it.")

            Path(ALICE_CONFIG_FILENAME).write_text(self._config_str, encoding="utf-8")

        def patch_compose(_compiler) -> None:
            compose_path = Path("output") / "docker-compose.yml"
            if not compose_path.exists():
                raise RuntimeError(
                    f"AliceLGService could not patch compose: {compose_path} does not exist."
                )

            compose = yaml.safe_load(compose_path.read_text(encoding="utf-8")) or {}
            services = compose.setdefault("services", {})
            services[ALICE_SERVICE_NAME] = {
                "image": "alicelg/alice:latest",
                "container_name": "alice-lg",
                "depends_on": ["brdnode_2_r100"],
                "extra_hosts": [DOCKER_HOST_ALIAS],
                "ports": [ALICE_HOST_PORT],
                "volumes": [f"{ALICE_CONFIG_PATH}:{ALICE_CONTAINER_CONFIG_PATH}:ro"],
            }

            compose_path.write_text(
                yaml.safe_dump(compose, sort_keys=False),
                encoding="utf-8",
            )

        return [write_config, patch_compose]
