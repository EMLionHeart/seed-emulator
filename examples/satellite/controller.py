#!/usr/bin/env python3
# encoding: utf-8

"""Runtime controller for the slot-based satellite link prototype."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional


DEFAULT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_DIR = DEFAULT_DIR / "configs"
VALID_CROSS_DIRECTIONS = {"left", "right"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Satellite Phase 2 slot-based dynamic link controller."
    )
    parser.add_argument(
        "--topology-config",
        default=str(DEFAULT_CONFIG_DIR / "topology.json"),
        help="Path to the topology inventory JSON file.",
    )
    parser.add_argument(
        "--link-state",
        default=str(DEFAULT_CONFIG_DIR / "link_state.json"),
        help="Path to the slot/link state JSON file.",
    )
    parser.add_argument(
        "--slot-name",
        help=(
            "Optional explicit slot name for attach/connect or owner-scoped "
            "release operations. If unset, the controller uses the first free "
            "or current occupied slot as appropriate."
        ),
    )
    parser.add_argument(
        "--init-slot-runtime",
        action="store_true",
        help=(
            "Reset and recreate all dynamic slot bridge networks, preconnect "
            "UT/GW fixed endpoints, and rewrite link_state.json."
        ),
    )
    parser.add_argument(
        "--show-link-state",
        action="store_true",
        help="Print the current slot/link state.",
    )
    parser.add_argument(
        "--attach-ut",
        help="Attach a UT to a satellite with one free access slot: <ut-id>:<satellite-id>.",
    )
    parser.add_argument(
        "--detach-ut",
        help="Release the currently occupied access slot of a UT: <ut-id>.",
    )
    parser.add_argument(
        "--attach-gw",
        help="Attach a gateway to a satellite with one free gateway slot: <gateway-id>:<satellite-id>.",
    )
    parser.add_argument(
        "--detach-gw",
        help="Release the currently occupied gateway slot of a gateway: <gateway-id>.",
    )
    parser.add_argument(
        "--connect-cross",
        help=(
            "Connect one orbit-a satellite and one orbit-b satellite through a "
            "free cross-plane slot: <sat-a>:<sat-b>:<dir-a>:<dir-b>."
        ),
    )
    parser.add_argument(
        "--release-slot",
        help="Release one occupied dynamic slot by slot name.",
    )

    args = parser.parse_args()

    action_count = sum(
        1
        for enabled in (
            args.init_slot_runtime,
            args.show_link_state,
            bool(args.attach_ut),
            bool(args.detach_ut),
            bool(args.attach_gw),
            bool(args.detach_gw),
            bool(args.connect_cross),
            bool(args.release_slot),
        )
        if enabled
    )
    if action_count > 1:
        parser.error("run only one action per command")
    if args.slot_name and not (
        args.attach_ut
        or args.detach_ut
        or args.attach_gw
        or args.detach_gw
        or args.connect_cross
    ):
        parser.error(
            "--slot-name currently applies only to --attach-ut, --detach-ut, "
            "--attach-gw, --detach-gw, or --connect-cross"
        )

    return args


def load_json(path_str: str) -> Dict[str, Any]:
    path = Path(path_str)
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def write_json(path_str: str, data: Dict[str, Any]) -> None:
    path = Path(path_str)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


class SatelliteController:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.topology = load_json(args.topology_config)
        self.link_state = self._load_link_state(args.link_state)

    def _default_link_state(self) -> Dict[str, Any]:
        return {
            "schema_version": "satellite-phase2-slot-state-v1",
            "runtime_initialized": False,
            "topology_schema_version": self.topology.get("schema_version"),
            "slots": [],
        }

    def _load_link_state(self, path_str: str) -> Dict[str, Any]:
        path = Path(path_str)
        if not path.exists():
            return self._default_link_state()
        return load_json(path_str)

    def _save_link_state(self, state: Dict[str, Any]) -> None:
        write_json(self.args.link_state, state)
        self.link_state = state

    def _slot_inventory(self) -> Dict[str, List[Dict[str, Any]]]:
        inventory = self.topology.get("slot_inventory", {})
        return {
            "ut_access": list(inventory.get("ut_access", [])),
            "gateway_access": list(inventory.get("gateway_access", [])),
            "cross_plane_isl": list(inventory.get("cross_plane_isl", [])),
        }

    def _slot_inventory_by_name(self) -> Dict[str, Dict[str, Any]]:
        entries: Dict[str, Dict[str, Any]] = {}
        for group in self._slot_inventory().values():
            for entry in group:
                entries[str(entry.get("slot_name"))] = entry
        return entries

    def _slot_state_by_name(self, state: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, Any]]:
        source = self.link_state if state is None else state
        return {
            str(entry.get("slot_name")): entry
            for entry in source.get("slots", [])
        }

    def _require_slot_type(
        self,
        *,
        state: Dict[str, Any],
        slot_name: str,
        expected_slot_type: str,
    ) -> Dict[str, Any]:
        slot_state = self._slot_state_by_name(state).get(slot_name)
        if slot_state is None:
            raise ValueError(f"unknown slot name: {slot_name}")
        if slot_state.get("slot_type") != expected_slot_type:
            raise RuntimeError(
                f"{slot_name} is {slot_state.get('slot_type')}, expected {expected_slot_type}"
            )
        return slot_state

    def _fixed_links(self) -> List[Dict[str, Any]]:
        return list(self.topology.get("fixed_links", {}).get("satellite_satellite", []))

    def _satellite_ids(self) -> set[str]:
        return {
            str(entry.get("satellite_id"))
            for entry in self.topology.get("satellites", [])
            if entry.get("satellite_id") is not None
        }

    def _gateway_ids(self) -> set[str]:
        return {
            str(entry.get("gateway_id"))
            for entry in self.topology.get("gateways", [])
            if entry.get("gateway_id") is not None
        }

    def _ut_ids(self) -> set[str]:
        return {
            str(entry.get("ut_id"))
            for entry in self.topology.get("user_terminals", [])
            if entry.get("ut_id") is not None
        }

    def _satellite_orbit(self, satellite_id: str) -> str:
        for entry in self.topology.get("satellites", []):
            if entry.get("satellite_id") == satellite_id:
                orbit_id = entry.get("orbit_id")
                if orbit_id is None:
                    break
                return str(orbit_id)
        raise ValueError(f"unknown satellite_id: {satellite_id}")

    def _build_initial_link_state(self) -> Dict[str, Any]:
        slots: List[Dict[str, Any]] = []

        for slot in self._slot_inventory().get("ut_access", []):
            pre = dict(slot.get("preconnected_endpoint", {}))
            slots.append(
                {
                    "slot_name": slot.get("slot_name"),
                    "slot_type": "ut_access",
                    "network_name": slot.get("network_name"),
                    "docker_network_name": slot.get("docker_network_name"),
                    "subnet": slot.get("subnet"),
                    "bridge_gateway_ip": slot.get("bridge_gateway_ip"),
                    "owner_node_id": slot.get("ut_id"),
                    "preconnected_endpoints": [pre],
                    "dynamic_endpoints": [],
                    "occupied": False,
                    "binding": None,
                }
            )

        for slot in self._slot_inventory().get("gateway_access", []):
            pre = dict(slot.get("preconnected_endpoint", {}))
            slots.append(
                {
                    "slot_name": slot.get("slot_name"),
                    "slot_type": "gw_access",
                    "network_name": slot.get("network_name"),
                    "docker_network_name": slot.get("docker_network_name"),
                    "subnet": slot.get("subnet"),
                    "bridge_gateway_ip": slot.get("bridge_gateway_ip"),
                    "owner_node_id": slot.get("gateway_id"),
                    "preconnected_endpoints": [pre],
                    "dynamic_endpoints": [],
                    "occupied": False,
                    "binding": None,
                }
            )

        for slot in self._slot_inventory().get("cross_plane_isl", []):
            slots.append(
                {
                    "slot_name": slot.get("slot_name"),
                    "slot_type": "cross_plane_isl",
                    "network_name": slot.get("network_name"),
                    "docker_network_name": slot.get("docker_network_name"),
                    "subnet": slot.get("subnet"),
                    "bridge_gateway_ip": slot.get("bridge_gateway_ip"),
                    "owner_node_id": None,
                    "preconnected_endpoints": [],
                    "dynamic_endpoints": [],
                    "occupied": False,
                    "binding": None,
                }
            )

        for link in self._fixed_links():
            slots.append(
                {
                    "slot_name": link.get("link_id"),
                    "slot_type": "fixed_isl",
                    "network_name": link.get("network_name"),
                    "docker_network_name": link.get("docker_network_name"),
                    "subnet": link.get("subnet"),
                    "bridge_gateway_ip": None,
                    "owner_node_id": None,
                    "preconnected_endpoints": list(link.get("endpoints", [])),
                    "dynamic_endpoints": [],
                    "occupied": True,
                    "binding": {
                        "satellite_a_id": link.get("endpoints", [{}])[0].get("node_id"),
                        "satellite_b_id": link.get("endpoints", [{}, {}])[1].get("node_id"),
                    },
                }
            )

        return {
            "schema_version": "satellite-phase2-slot-state-v1",
            "runtime_initialized": True,
            "topology_schema_version": self.topology.get("schema_version"),
            "slots": sorted(slots, key=lambda entry: str(entry.get("slot_name", ""))),
        }

    def _run_command(
        self,
        command: List[str],
        *,
        failure_stage: str,
        allow_failure: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        rendered = shlex.join(command)
        print(f"  {rendered}")
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.stdout.strip():
            print(result.stdout.rstrip())
        if result.stderr.strip():
            print(result.stderr.rstrip())
        if result.returncode != 0 and not allow_failure:
            raise RuntimeError(
                f"{failure_stage}: command failed with exit status {result.returncode}: {rendered}"
            )
        return result

    def _docker_network_exists(self, docker_network_name: str) -> bool:
        result = subprocess.run(
            ["docker", "network", "inspect", docker_network_name],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0

    def _docker_network_connected_nodes(self, docker_network_name: str) -> List[str]:
        if not self._docker_network_exists(docker_network_name):
            return []
        result = subprocess.run(
            ["docker", "network", "inspect", docker_network_name],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"inspect docker network failed: {docker_network_name}")
        payload = json.loads(result.stdout)
        containers = payload[0].get("Containers") or {}
        connected: List[str] = []
        for container in containers.values():
            name = container.get("Name")
            if name:
                connected.append(str(name))
        return sorted(set(connected))

    def _remove_docker_network_force(self, docker_network_name: str) -> None:
        if not self._docker_network_exists(docker_network_name):
            return
        for node_id in self._docker_network_connected_nodes(docker_network_name):
            self._run_command(
                ["docker", "network", "disconnect", "-f", docker_network_name, node_id],
                failure_stage=f"disconnect {node_id} from {docker_network_name}",
                allow_failure=True,
            )
        self._run_command(
            ["docker", "network", "rm", docker_network_name],
            failure_stage=f"remove docker network {docker_network_name}",
        )

    def _list_interfaces(self, node_id: str) -> set[str]:
        result = self._run_command(
            ["docker", "exec", node_id, "ip", "-o", "link", "show"],
            failure_stage=f"capture interfaces for {node_id}",
        )
        interface_names: set[str] = set()
        for line in result.stdout.splitlines():
            if ": " not in line:
                continue
            iface_with_peer = line.split(": ", 1)[1].split(":", 1)[0].strip()
            iface_name = iface_with_peer.split("@", 1)[0]
            if iface_name:
                interface_names.add(iface_name)
        return interface_names

    def _interface_exists(self, *, node_id: str, interface_name: str) -> bool:
        result = subprocess.run(
            ["docker", "exec", node_id, "ip", "link", "show", "dev", interface_name],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0

    def _delete_interface_if_present(self, *, node_id: str, interface_name: str) -> bool:
        if not self._interface_exists(node_id=node_id, interface_name=interface_name):
            return False
        self._run_command(
            ["docker", "exec", node_id, "ip", "link", "del", interface_name],
            failure_stage=f"delete stale interface {interface_name} on {node_id}",
        )
        if self._interface_exists(node_id=node_id, interface_name=interface_name):
            raise RuntimeError(
                f"stale interface cleanup failed: {interface_name} still exists on {node_id}"
            )
        return True

    def _connect_and_discover_new_interface(
        self,
        *,
        docker_network_name: str,
        node_id: str,
    ) -> str:
        before = self._list_interfaces(node_id)
        self._run_command(
            ["docker", "network", "connect", docker_network_name, node_id],
            failure_stage=f"connect {node_id} to {docker_network_name}",
        )
        after = self._list_interfaces(node_id)
        new_ifaces = sorted(after - before)
        if len(new_ifaces) != 1:
            raise RuntimeError(
                f"discover new interface for {node_id}: expected exactly one new "
                f"interface, found {len(new_ifaces)} ({', '.join(new_ifaces) or 'none'})"
            )
        return new_ifaces[0]

    def _configure_runtime_interface(
        self,
        *,
        node_id: str,
        new_iface: str,
        target_iface_name: str,
        target_ip: str,
    ) -> None:
        self._run_command(
            ["docker", "exec", node_id, "ip", "link", "set", new_iface, "down"],
            failure_stage=f"set {new_iface} down on {node_id}",
        )
        self._run_command(
            [
                "docker",
                "exec",
                node_id,
                "ip",
                "link",
                "set",
                new_iface,
                "name",
                target_iface_name,
            ],
            failure_stage=f"rename {new_iface} to {target_iface_name} on {node_id}",
        )
        self._run_command(
            ["docker", "exec", node_id, "ip", "addr", "flush", "dev", target_iface_name],
            failure_stage=f"flush addresses on {target_iface_name} in {node_id}",
        )
        self._run_command(
            [
                "docker",
                "exec",
                node_id,
                "ip",
                "addr",
                "add",
                target_ip,
                "dev",
                target_iface_name,
            ],
            failure_stage=f"assign {target_ip} to {target_iface_name} in {node_id}",
        )
        self._run_command(
            ["docker", "exec", node_id, "ip", "link", "set", target_iface_name, "up"],
            failure_stage=f"bring {target_iface_name} up in {node_id}",
        )

    def _validate_interface_ip(
        self,
        *,
        node_id: str,
        interface_name: str,
        expected_ip: str,
    ) -> bool:
        result = subprocess.run(
            ["docker", "exec", node_id, "ip", "-o", "addr", "show", "dev", interface_name],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return False
        return expected_ip in result.stdout

    def _ping(self, *, node_id: str, target_ip: str) -> None:
        self._run_command(
            ["docker", "exec", node_id, "ping", "-c", "3", target_ip],
            failure_stage=f"ping {target_ip} from {node_id}",
        )

    def _slot_dynamic_candidates(self, slot: Dict[str, Any]) -> List[Dict[str, str]]:
        slot_type = str(slot.get("slot_type"))
        if slot_type in {"ut_access", "gw_access"}:
            dynamic_template = dict(slot.get("dynamic_endpoint_template", {}))
            return [
                {
                    "node_id": satellite_id,
                    "interface_name": str(dynamic_template.get("interface_name")),
                }
                for satellite_id in slot.get("candidate_satellite_ids", [])
            ]
        if slot_type == "cross_plane_isl":
            endpoint_a_template = dict(slot.get("endpoint_a_template", {}))
            endpoint_b_template = dict(slot.get("endpoint_b_template", {}))
            candidates: List[Dict[str, str]] = []
            candidates.extend(
                {
                    "node_id": satellite_id,
                    "interface_name": str(endpoint_a_template.get("interface_name")),
                }
                for satellite_id in slot.get("orbit_a_satellite_ids", [])
            )
            candidates.extend(
                {
                    "node_id": satellite_id,
                    "interface_name": str(endpoint_b_template.get("interface_name")),
                }
                for satellite_id in slot.get("orbit_b_satellite_ids", [])
            )
            return candidates
        return []

    def _cleanup_slot_runtime_artifacts(self, slot: Dict[str, Any]) -> None:
        docker_network_name = str(slot.get("docker_network_name"))
        self._remove_docker_network_force(docker_network_name)

        preconnected_endpoint = slot.get("preconnected_endpoint")
        if isinstance(preconnected_endpoint, dict):
            node_id = preconnected_endpoint.get("node_id")
            interface_name = preconnected_endpoint.get("interface_name")
            if node_id and interface_name:
                self._delete_interface_if_present(
                    node_id=str(node_id),
                    interface_name=str(interface_name),
                )

        for candidate in self._slot_dynamic_candidates(slot):
            self._delete_interface_if_present(
                node_id=str(candidate["node_id"]),
                interface_name=str(candidate["interface_name"]),
            )

    def _create_slot_network(self, slot: Dict[str, Any]) -> None:
        self._run_command(
            [
                "docker",
                "network",
                "create",
                "--driver",
                "bridge",
                "--internal",
                "--subnet",
                str(slot.get("subnet")),
                "--gateway",
                str(slot.get("bridge_gateway_ip")),
                str(slot.get("docker_network_name")),
            ],
            failure_stage=f"create slot network {slot.get('docker_network_name')}",
        )

    def _connect_and_configure_endpoint(
        self,
        *,
        docker_network_name: str,
        endpoint: Dict[str, Any],
    ) -> None:
        node_id = str(endpoint.get("node_id"))
        interface_name = str(endpoint.get("interface_name"))
        ip_address = str(endpoint.get("ip_address"))
        if self._interface_exists(node_id=node_id, interface_name=interface_name):
            raise RuntimeError(
                f"refusing to reuse uncertain interface {interface_name} on {node_id}"
            )
        new_iface = self._connect_and_discover_new_interface(
            docker_network_name=docker_network_name,
            node_id=node_id,
        )
        if new_iface == interface_name:
            raise RuntimeError(
                f"pre-configure validation: unexpected interface-name collision "
                f"for target name {interface_name} on {node_id}"
            )
        self._configure_runtime_interface(
            node_id=node_id,
            new_iface=new_iface,
            target_iface_name=interface_name,
            target_ip=ip_address,
        )
        if not self._validate_interface_ip(
            node_id=node_id,
            interface_name=interface_name,
            expected_ip=ip_address,
        ):
            raise RuntimeError(
                f"interface validation failed for {interface_name} on {node_id}"
            )

    def _rollback_dynamic_endpoint(
        self,
        *,
        docker_network_name: str,
        endpoint: Dict[str, Any],
    ) -> None:
        node_id = str(endpoint.get("node_id"))
        interface_name = str(endpoint.get("interface_name"))
        self._run_command(
            ["docker", "network", "disconnect", "-f", docker_network_name, node_id],
            failure_stage=f"rollback disconnect {node_id} from {docker_network_name}",
            allow_failure=True,
        )
        self._delete_interface_if_present(
            node_id=node_id,
            interface_name=interface_name,
        )

    def _require_initialized_state(self) -> Dict[str, Any]:
        if not self.link_state.get("runtime_initialized", False):
            raise RuntimeError(
                "slot runtime is not initialized; run --init-slot-runtime first"
            )
        return self.link_state

    def _replace_slot_state_entry(
        self,
        *,
        state: Dict[str, Any],
        updated_entry: Dict[str, Any],
    ) -> Dict[str, Any]:
        new_slots = [
            updated_entry if entry.get("slot_name") == updated_entry.get("slot_name") else entry
            for entry in state.get("slots", [])
        ]
        new_state = dict(state)
        new_state["slots"] = new_slots
        return new_state

    def _validate_preconnected_slot_endpoint(self, slot_name: str) -> None:
        slot_inventory = self._slot_inventory_by_name().get(slot_name)
        if slot_inventory is None:
            raise ValueError(f"unknown slot inventory: {slot_name}")
        pre = slot_inventory.get("preconnected_endpoint")
        if not isinstance(pre, dict):
            return
        if not self._docker_network_exists(str(slot_inventory.get("docker_network_name"))):
            raise RuntimeError(
                f"slot bridge missing for {slot_name}; rerun --init-slot-runtime"
            )
        if not self._validate_interface_ip(
            node_id=str(pre.get("node_id")),
            interface_name=str(pre.get("interface_name")),
            expected_ip=str(pre.get("ip_address")),
        ):
            raise RuntimeError(
                f"preconnected endpoint validation failed for {slot_name}; rerun --init-slot-runtime"
            )

    def _select_owner_slot_for_attach(
        self,
        *,
        state: Dict[str, Any],
        owner_slot_type: str,
        owner_node_id: str,
        requested_slot_name: Optional[str],
    ) -> Dict[str, Any]:
        slot_states = [
            entry
            for entry in state.get("slots", [])
            if entry.get("slot_type") == owner_slot_type
            and entry.get("owner_node_id") == owner_node_id
        ]
        occupied_slots = [entry for entry in slot_states if entry.get("occupied", False)]
        if occupied_slots:
            raise RuntimeError(
                f"{owner_node_id} already occupies {occupied_slots[0].get('slot_name')}; release it first"
            )

        if requested_slot_name is not None:
            slot_state = self._require_slot_type(
                state=state,
                slot_name=requested_slot_name,
                expected_slot_type=owner_slot_type,
            )
            if slot_state.get("owner_node_id") != owner_node_id:
                raise RuntimeError(
                    f"{requested_slot_name} is not owned by {owner_node_id}"
                )
            if slot_state.get("occupied", False):
                raise RuntimeError(f"{requested_slot_name} is already occupied")
            return slot_state

        free_slots = [
            entry
            for entry in sorted(slot_states, key=lambda item: str(item.get("slot_name")))
            if not entry.get("occupied", False)
        ]
        if not free_slots:
            raise RuntimeError(f"no free slot for {owner_node_id}")
        return free_slots[0]

    def _select_owner_slot_for_release(
        self,
        *,
        state: Dict[str, Any],
        owner_slot_type: str,
        owner_node_id: str,
        requested_slot_name: Optional[str],
    ) -> Dict[str, Any]:
        slot_states = [
            entry
            for entry in state.get("slots", [])
            if entry.get("slot_type") == owner_slot_type
            and entry.get("owner_node_id") == owner_node_id
        ]

        if requested_slot_name is not None:
            slot_state = self._require_slot_type(
                state=state,
                slot_name=requested_slot_name,
                expected_slot_type=owner_slot_type,
            )
            if slot_state.get("owner_node_id") != owner_node_id:
                raise RuntimeError(
                    f"{requested_slot_name} is not owned by {owner_node_id}"
                )
            if not slot_state.get("occupied", False):
                raise RuntimeError(f"{requested_slot_name} is not occupied")
            return slot_state

        occupied_slots = [
            entry for entry in slot_states if entry.get("occupied", False)
        ]
        if not occupied_slots:
            raise RuntimeError(f"{owner_node_id} has no occupied slot")
        return occupied_slots[0]

    def _select_cross_plane_slot_for_connect(
        self,
        *,
        state: Dict[str, Any],
        requested_slot_name: Optional[str],
    ) -> Dict[str, Any]:
        if requested_slot_name is not None:
            slot_state = self._require_slot_type(
                state=state,
                slot_name=requested_slot_name,
                expected_slot_type="cross_plane_isl",
            )
            if slot_state.get("occupied", False):
                raise RuntimeError(f"{requested_slot_name} is already occupied")
            return slot_state

        free_slots = [
            entry
            for entry in sorted(
                [
                    slot
                    for slot in state.get("slots", [])
                    if slot.get("slot_type") == "cross_plane_isl"
                ],
                key=lambda item: str(item.get("slot_name")),
            )
            if not entry.get("occupied", False)
        ]
        if not free_slots:
            raise RuntimeError("no free cross-plane slot")
        return free_slots[0]

    def initialize_slot_runtime(self) -> Dict[str, Any]:
        print("Initializing slot runtime")
        slot_inventory = self._slot_inventory()

        for slot_group in (
            slot_inventory.get("ut_access", []),
            slot_inventory.get("gateway_access", []),
            slot_inventory.get("cross_plane_isl", []),
        ):
            for slot in slot_group:
                print(f"Rebuilding slot {slot.get('slot_name')}")
                self._cleanup_slot_runtime_artifacts(slot)
                self._create_slot_network(slot)
                preconnected_endpoint = slot.get("preconnected_endpoint")
                if isinstance(preconnected_endpoint, dict):
                    self._connect_and_configure_endpoint(
                        docker_network_name=str(slot.get("docker_network_name")),
                        endpoint=preconnected_endpoint,
                    )

        new_state = self._build_initial_link_state()
        self._save_link_state(new_state)
        return new_state

    def _parse_ut_attach_request(self, attach_request: str) -> Dict[str, str]:
        parts = attach_request.split(":", 1)
        if len(parts) != 2:
            raise ValueError("attach-ut expects <ut-id>:<satellite-id>")
        ut_id, satellite_id = parts
        if ut_id not in self._ut_ids():
            raise ValueError(f"unknown ut_id: {ut_id}")
        if satellite_id not in self._satellite_ids():
            raise ValueError(f"unknown satellite_id: {satellite_id}")
        return {"ut_id": ut_id, "satellite_id": satellite_id}

    def _parse_gw_attach_request(self, attach_request: str) -> Dict[str, str]:
        parts = attach_request.split(":", 1)
        if len(parts) != 2:
            raise ValueError("attach-gw expects <gateway-id>:<satellite-id>")
        gateway_id, satellite_id = parts
        if gateway_id not in self._gateway_ids():
            raise ValueError(f"unknown gateway_id: {gateway_id}")
        if satellite_id not in self._satellite_ids():
            raise ValueError(f"unknown satellite_id: {satellite_id}")
        return {"gateway_id": gateway_id, "satellite_id": satellite_id}

    def _parse_cross_request(self, request: str) -> Dict[str, str]:
        parts = request.split(":")
        if len(parts) != 4:
            raise ValueError(
                "connect-cross expects <sat-a>:<sat-b>:<dir-a>:<dir-b>"
            )
        first_satellite_id, second_satellite_id, first_direction, second_direction = parts
        if first_direction not in VALID_CROSS_DIRECTIONS:
            raise ValueError(f"invalid direction: {first_direction}")
        if second_direction not in VALID_CROSS_DIRECTIONS:
            raise ValueError(f"invalid direction: {second_direction}")
        if first_satellite_id not in self._satellite_ids():
            raise ValueError(f"unknown satellite_id: {first_satellite_id}")
        if second_satellite_id not in self._satellite_ids():
            raise ValueError(f"unknown satellite_id: {second_satellite_id}")

        first_orbit = self._satellite_orbit(first_satellite_id)
        second_orbit = self._satellite_orbit(second_satellite_id)
        if first_orbit == second_orbit:
            raise ValueError("cross-plane link requires satellites from different orbits")

        if first_orbit == "orbit-a":
            return {
                "satellite_a_id": first_satellite_id,
                "satellite_b_id": second_satellite_id,
                "satellite_a_direction": first_direction,
                "satellite_b_direction": second_direction,
            }
        return {
            "satellite_a_id": second_satellite_id,
            "satellite_b_id": first_satellite_id,
            "satellite_a_direction": second_direction,
            "satellite_b_direction": first_direction,
        }

    def attach_ut(
        self,
        attach_request: str,
        *,
        requested_slot_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        state = self._require_initialized_state()
        request = self._parse_ut_attach_request(attach_request)
        ut_id = request["ut_id"]
        satellite_id = request["satellite_id"]
        slot_state = self._select_owner_slot_for_attach(
            state=state,
            owner_slot_type="ut_access",
            owner_node_id=ut_id,
            requested_slot_name=requested_slot_name,
        )
        slot_name = str(slot_state.get("slot_name"))
        slot_inventory = self._slot_inventory_by_name()[slot_name]
        self._validate_preconnected_slot_endpoint(slot_name)

        template = dict(slot_inventory.get("dynamic_endpoint_template", {}))
        dynamic_endpoint = {
            "node_id": satellite_id,
            "node_type": template.get("node_type"),
            "interface_name": template.get("interface_name"),
            "ip_address": template.get("ip_address"),
        }
        satellite_target_ip = str(template.get("ip_address")).split("/", 1)[0]
        try:
            self._connect_and_configure_endpoint(
                docker_network_name=str(slot_inventory.get("docker_network_name")),
                endpoint=dynamic_endpoint,
            )
            self._ping(node_id=ut_id, target_ip=satellite_target_ip)
        except RuntimeError:
            self._rollback_dynamic_endpoint(
                docker_network_name=str(slot_inventory.get("docker_network_name")),
                endpoint=dynamic_endpoint,
            )
            raise

        updated_entry = dict(slot_state)
        updated_entry["dynamic_endpoints"] = [dynamic_endpoint]
        updated_entry["occupied"] = True
        updated_entry["binding"] = {
            "ut_id": ut_id,
            "satellite_id": satellite_id,
        }
        self._save_link_state(
            self._replace_slot_state_entry(state=state, updated_entry=updated_entry)
        )
        return updated_entry

    def detach_ut(
        self,
        ut_id: str,
        *,
        requested_slot_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        state = self._require_initialized_state()
        if ut_id not in self._ut_ids():
            raise ValueError(f"unknown ut_id: {ut_id}")
        slot_state = self._select_owner_slot_for_release(
            state=state,
            owner_slot_type="ut_access",
            owner_node_id=ut_id,
            requested_slot_name=requested_slot_name,
        )
        slot_name = str(slot_state.get("slot_name"))
        slot_inventory = self._slot_inventory_by_name()[slot_name]
        dynamic_endpoints = list(slot_state.get("dynamic_endpoints", []))
        if len(dynamic_endpoints) != 1:
            raise RuntimeError(f"{slot_name} has unexpected dynamic endpoint count")
        dynamic_endpoint = dynamic_endpoints[0]

        self._run_command(
            [
                "docker",
                "network",
                "disconnect",
                "-f",
                str(slot_inventory.get("docker_network_name")),
                str(dynamic_endpoint.get("node_id")),
            ],
            failure_stage=f"disconnect {dynamic_endpoint.get('node_id')} from {slot_name}",
        )
        self._delete_interface_if_present(
            node_id=str(dynamic_endpoint.get("node_id")),
            interface_name=str(dynamic_endpoint.get("interface_name")),
        )

        updated_entry = dict(slot_state)
        updated_entry["dynamic_endpoints"] = []
        updated_entry["occupied"] = False
        updated_entry["binding"] = None
        self._save_link_state(
            self._replace_slot_state_entry(state=state, updated_entry=updated_entry)
        )
        return updated_entry

    def attach_gateway(
        self,
        attach_request: str,
        *,
        requested_slot_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        state = self._require_initialized_state()
        request = self._parse_gw_attach_request(attach_request)
        gateway_id = request["gateway_id"]
        satellite_id = request["satellite_id"]
        slot_state = self._select_owner_slot_for_attach(
            state=state,
            owner_slot_type="gw_access",
            owner_node_id=gateway_id,
            requested_slot_name=requested_slot_name,
        )
        slot_name = str(slot_state.get("slot_name"))
        slot_inventory = self._slot_inventory_by_name()[slot_name]
        self._validate_preconnected_slot_endpoint(slot_name)

        template = dict(slot_inventory.get("dynamic_endpoint_template", {}))
        dynamic_endpoint = {
            "node_id": satellite_id,
            "node_type": template.get("node_type"),
            "interface_name": template.get("interface_name"),
            "ip_address": template.get("ip_address"),
        }
        satellite_target_ip = str(template.get("ip_address")).split("/", 1)[0]
        try:
            self._connect_and_configure_endpoint(
                docker_network_name=str(slot_inventory.get("docker_network_name")),
                endpoint=dynamic_endpoint,
            )
            self._ping(node_id=gateway_id, target_ip=satellite_target_ip)
        except RuntimeError:
            self._rollback_dynamic_endpoint(
                docker_network_name=str(slot_inventory.get("docker_network_name")),
                endpoint=dynamic_endpoint,
            )
            raise

        updated_entry = dict(slot_state)
        updated_entry["dynamic_endpoints"] = [dynamic_endpoint]
        updated_entry["occupied"] = True
        updated_entry["binding"] = {
            "gateway_id": gateway_id,
            "satellite_id": satellite_id,
        }
        self._save_link_state(
            self._replace_slot_state_entry(state=state, updated_entry=updated_entry)
        )
        return updated_entry

    def detach_gateway(
        self,
        gateway_id: str,
        *,
        requested_slot_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        state = self._require_initialized_state()
        if gateway_id not in self._gateway_ids():
            raise ValueError(f"unknown gateway_id: {gateway_id}")
        slot_state = self._select_owner_slot_for_release(
            state=state,
            owner_slot_type="gw_access",
            owner_node_id=gateway_id,
            requested_slot_name=requested_slot_name,
        )
        slot_name = str(slot_state.get("slot_name"))
        slot_inventory = self._slot_inventory_by_name()[slot_name]
        dynamic_endpoints = list(slot_state.get("dynamic_endpoints", []))
        if len(dynamic_endpoints) != 1:
            raise RuntimeError(f"{slot_name} has unexpected dynamic endpoint count")
        dynamic_endpoint = dynamic_endpoints[0]

        self._run_command(
            [
                "docker",
                "network",
                "disconnect",
                "-f",
                str(slot_inventory.get("docker_network_name")),
                str(dynamic_endpoint.get("node_id")),
            ],
            failure_stage=f"disconnect {dynamic_endpoint.get('node_id')} from {slot_name}",
        )
        self._delete_interface_if_present(
            node_id=str(dynamic_endpoint.get("node_id")),
            interface_name=str(dynamic_endpoint.get("interface_name")),
        )

        updated_entry = dict(slot_state)
        updated_entry["dynamic_endpoints"] = []
        updated_entry["occupied"] = False
        updated_entry["binding"] = None
        self._save_link_state(
            self._replace_slot_state_entry(state=state, updated_entry=updated_entry)
        )
        return updated_entry

    def connect_cross_plane(
        self,
        request_text: str,
        *,
        requested_slot_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        state = self._require_initialized_state()
        request = self._parse_cross_request(request_text)
        satellite_a_id = request["satellite_a_id"]
        satellite_b_id = request["satellite_b_id"]
        satellite_a_direction = request["satellite_a_direction"]
        satellite_b_direction = request["satellite_b_direction"]

        for entry in state.get("slots", []):
            if entry.get("slot_type") != "cross_plane_isl":
                continue
            if not entry.get("occupied", False):
                continue
            binding = entry.get("binding") or {}
            if satellite_a_id in {binding.get("satellite_a_id"), binding.get("satellite_b_id")}:
                raise RuntimeError(f"{satellite_a_id} already uses cross-plane slot {entry.get('slot_name')}")
            if satellite_b_id in {binding.get("satellite_a_id"), binding.get("satellite_b_id")}:
                raise RuntimeError(f"{satellite_b_id} already uses cross-plane slot {entry.get('slot_name')}")

        slot_state = self._select_cross_plane_slot_for_connect(
            state=state,
            requested_slot_name=requested_slot_name,
        )
        slot_name = str(slot_state.get("slot_name"))
        slot_inventory = self._slot_inventory_by_name()[slot_name]
        docker_network_name = str(slot_inventory.get("docker_network_name"))
        if not self._docker_network_exists(docker_network_name):
            raise RuntimeError(
                f"slot bridge missing for {slot_name}; rerun --init-slot-runtime"
            )

        endpoint_a_template = dict(slot_inventory.get("endpoint_a_template", {}))
        endpoint_b_template = dict(slot_inventory.get("endpoint_b_template", {}))
        endpoint_a = {
            "node_id": satellite_a_id,
            "node_type": endpoint_a_template.get("node_type"),
            "interface_name": endpoint_a_template.get("interface_name"),
            "ip_address": endpoint_a_template.get("ip_address"),
            "logical_direction": satellite_a_direction,
        }
        endpoint_b = {
            "node_id": satellite_b_id,
            "node_type": endpoint_b_template.get("node_type"),
            "interface_name": endpoint_b_template.get("interface_name"),
            "ip_address": endpoint_b_template.get("ip_address"),
            "logical_direction": satellite_b_direction,
        }
        satellite_b_target_ip = str(endpoint_b_template.get("ip_address")).split("/", 1)[0]

        endpoint_a_connected = False
        endpoint_b_connected = False
        try:
            self._connect_and_configure_endpoint(
                docker_network_name=docker_network_name,
                endpoint=endpoint_a,
            )
            endpoint_a_connected = True
            self._connect_and_configure_endpoint(
                docker_network_name=docker_network_name,
                endpoint=endpoint_b,
            )
            endpoint_b_connected = True
            self._ping(node_id=satellite_a_id, target_ip=satellite_b_target_ip)
        except RuntimeError:
            if endpoint_b_connected:
                self._rollback_dynamic_endpoint(
                    docker_network_name=docker_network_name,
                    endpoint=endpoint_b,
                )
            if endpoint_a_connected:
                self._rollback_dynamic_endpoint(
                    docker_network_name=docker_network_name,
                    endpoint=endpoint_a,
                )
            raise

        updated_entry = dict(slot_state)
        updated_entry["dynamic_endpoints"] = [endpoint_a, endpoint_b]
        updated_entry["occupied"] = True
        updated_entry["binding"] = {
            "satellite_a_id": satellite_a_id,
            "satellite_b_id": satellite_b_id,
            "satellite_a_direction": satellite_a_direction,
            "satellite_b_direction": satellite_b_direction,
        }
        self._save_link_state(
            self._replace_slot_state_entry(state=state, updated_entry=updated_entry)
        )
        return updated_entry

    def release_slot(self, slot_name: str) -> Dict[str, Any]:
        state = self._require_initialized_state()
        slot_states = self._slot_state_by_name(state)
        slot_inventory = self._slot_inventory_by_name()

        slot_state = slot_states.get(slot_name)
        if slot_state is None:
            raise ValueError(f"unknown slot name: {slot_name}")
        if slot_state.get("slot_type") == "fixed_isl":
            raise RuntimeError(f"{slot_name} is fixed_isl and cannot be released")
        if not slot_state.get("occupied", False):
            return slot_state

        slot_inventory_entry = slot_inventory.get(slot_name)
        if slot_inventory_entry is None:
            raise ValueError(f"missing slot inventory for {slot_name}")
        docker_network_name = str(slot_inventory_entry.get("docker_network_name"))
        dynamic_endpoints = list(slot_state.get("dynamic_endpoints", []))
        errors: List[str] = []

        for endpoint in dynamic_endpoints:
            node_id = str(endpoint.get("node_id"))
            interface_name = str(endpoint.get("interface_name"))
            result = self._run_command(
                ["docker", "network", "disconnect", "-f", docker_network_name, node_id],
                failure_stage=f"disconnect {node_id} from {slot_name}",
                allow_failure=True,
            )
            if result.returncode != 0:
                errors.append(f"disconnect_failed:{node_id}")
                continue
            try:
                self._delete_interface_if_present(
                    node_id=node_id,
                    interface_name=interface_name,
                )
            except RuntimeError as error:
                errors.append(str(error))

        if errors:
            raise RuntimeError(
                f"slot release incomplete for {slot_name}; state not updated: {'; '.join(errors)}"
            )

        updated_entry = dict(slot_state)
        updated_entry["dynamic_endpoints"] = []
        updated_entry["occupied"] = False
        updated_entry["binding"] = None
        self._save_link_state(
            self._replace_slot_state_entry(state=state, updated_entry=updated_entry)
        )
        return updated_entry

    def print_summary(self) -> None:
        slot_inventory = self._slot_inventory()
        print("Satellite slot-link prototype")
        print(f"  topology: {self.args.topology_config}")
        print(f"  link_state: {self.args.link_state}")
        print(f"  topology schema: {self.topology.get('schema_version', 'unknown')}")
        print(
            "  node counts: "
            f"satellites={len(self.topology.get('satellites', []))}, "
            f"gateways={len(self.topology.get('gateways', []))}, "
            f"user_terminals={len(self.topology.get('user_terminals', []))}"
        )
        print(
            "  slot inventory: "
            f"ut_access={len(slot_inventory.get('ut_access', []))}, "
            f"gateway_access={len(slot_inventory.get('gateway_access', []))}, "
            f"cross_plane_isl={len(slot_inventory.get('cross_plane_isl', []))}, "
            f"fixed_isl={len(self._fixed_links())}"
        )
        print(
            "  runtime initialized: "
            f"{self.link_state.get('runtime_initialized', False)}"
        )

    def print_link_state(self) -> None:
        state = self.link_state
        print("Link state")
        print(f"  schema: {state.get('schema_version', 'unknown')}")
        print(f"  runtime_initialized: {state.get('runtime_initialized', False)}")
        for entry in state.get("slots", []):
            print(
                "  "
                f"{entry.get('slot_name')} "
                f"type={entry.get('slot_type')} "
                f"occupied={entry.get('occupied')}"
            )
            print(
                "    "
                f"network={entry.get('docker_network_name')} "
                f"subnet={entry.get('subnet')}"
            )
            preconnected = entry.get("preconnected_endpoints", [])
            if preconnected:
                joined = ", ".join(
                    f"{endpoint.get('node_id')}:{endpoint.get('interface_name')}:{endpoint.get('ip_address')}"
                    for endpoint in preconnected
                )
                print(f"    preconnected={joined}")
            dynamic = entry.get("dynamic_endpoints", [])
            if dynamic:
                joined = ", ".join(
                    f"{endpoint.get('node_id')}:{endpoint.get('interface_name')}:{endpoint.get('ip_address')}"
                    + (
                        f":{endpoint.get('logical_direction')}"
                        if endpoint.get("logical_direction") is not None
                        else ""
                    )
                    for endpoint in dynamic
                )
                print(f"    dynamic={joined}")
            binding = entry.get("binding")
            if binding:
                print(f"    binding={json.dumps(binding, sort_keys=True)}")

    def run(self) -> int:
        self.print_summary()
        try:
            if self.args.init_slot_runtime:
                state = self.initialize_slot_runtime()
                print(f"slot runtime initialized: {len(state.get('slots', []))} state entries")
                return 0
            if self.args.show_link_state:
                self.print_link_state()
                return 0
            if self.args.attach_ut:
                result = self.attach_ut(
                    self.args.attach_ut,
                    requested_slot_name=self.args.slot_name,
                )
                print(f"ut attached via {result.get('slot_name')}")
                return 0
            if self.args.detach_ut:
                result = self.detach_ut(
                    self.args.detach_ut,
                    requested_slot_name=self.args.slot_name,
                )
                print(f"ut slot released: {result.get('slot_name')}")
                return 0
            if self.args.attach_gw:
                result = self.attach_gateway(
                    self.args.attach_gw,
                    requested_slot_name=self.args.slot_name,
                )
                print(f"gateway attached via {result.get('slot_name')}")
                return 0
            if self.args.detach_gw:
                result = self.detach_gateway(
                    self.args.detach_gw,
                    requested_slot_name=self.args.slot_name,
                )
                print(f"gateway slot released: {result.get('slot_name')}")
                return 0
            if self.args.connect_cross:
                result = self.connect_cross_plane(
                    self.args.connect_cross,
                    requested_slot_name=self.args.slot_name,
                )
                print(f"cross-plane slot occupied: {result.get('slot_name')}")
                return 0
            if self.args.release_slot:
                result = self.release_slot(self.args.release_slot)
                print(f"slot released: {result.get('slot_name')}")
                return 0
        except (ValueError, RuntimeError) as error:
            print(f"Controller action failed: {error}")
            return 1

        print("No action requested.")
        return 0


def main() -> int:
    args = parse_args()
    controller = SatelliteController(args)
    return controller.run()


if __name__ == "__main__":
    raise SystemExit(main())
