#!/usr/bin/env python3
# encoding: utf-8

"""Build the slot-based satellite prototype topology.

This Phase 2 prototype keeps the topology intentionally small:

- 3 user terminals
- 6 satellites split across two orbits
- 3 gateways
- fixed intra-plane ISLs compiled by SEED
- slot-based dynamic UT/GW/cross-plane links managed at runtime by controller.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Tuple

from seedemu.compiler import Docker, Platform
from seedemu.core import Emulator, OptionMode, OptionRegistry
from seedemu.layers import Base, Ebgp, Ibgp, Ospf, PeerRelationship, Routing


SATELLITE_ASN = 250
SATELLITE_OPERATOR_ASN = 65000
MINI_INTERNET_HOSTS_PER_STUB_AS = 2

DEFAULT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = DEFAULT_DIR / "output"
DEFAULT_CONFIG_DIR = DEFAULT_DIR / "configs"
DEFAULT_TOPOLOGY_JSON = DEFAULT_CONFIG_DIR / "topology.json"

ACCESS_SLOT_COUNT = 2
GATEWAY_SLOT_COUNT = 2
CROSS_PLANE_SLOT_COUNT = 3

SATELLITES = (
    "sat-a1",
    "sat-a2",
    "sat-a3",
    "sat-b1",
    "sat-b2",
    "sat-b3",
)

GATEWAYS = (
    "gw1",
    "gw2",
    "gw3",
)

USER_TERMINALS = (
    "ut1",
    "ut2",
    "ut3",
)

ORBIT_A_SATELLITES = ("sat-a1", "sat-a2", "sat-a3")
ORBIT_B_SATELLITES = ("sat-b1", "sat-b2", "sat-b3")

FIXED_INTRA_ORBIT_LINKS = (
    (
        "sat-a1",
        "sat-a2",
        "a1-a2",
        "10.250.201.0/30",
        "10.250.201.1",
        "10.250.201.2",
        "front",
        "back",
        "orbit-a",
    ),
    (
        "sat-a2",
        "sat-a3",
        "a2-a3",
        "10.250.202.0/30",
        "10.250.202.1",
        "10.250.202.2",
        "front",
        "back",
        "orbit-a",
    ),
    (
        "sat-b1",
        "sat-b2",
        "b1-b2",
        "10.250.203.0/30",
        "10.250.203.1",
        "10.250.203.2",
        "front",
        "back",
        "orbit-b",
    ),
    (
        "sat-b2",
        "sat-b3",
        "b2-b3",
        "10.250.204.0/30",
        "10.250.204.1",
        "10.250.204.2",
        "front",
        "back",
        "orbit-b",
    ),
)

MINI_INTERNET_IX_DISPLAY_NAMES = {
    100: "NYC-100",
    101: "San Jose-101",
    102: "Chicago-102",
    103: "Miami-103",
    104: "Boston-104",
    105: "Huston-105",
}

MINI_INTERNET_TRANSIT_AS_LAYOUT = {
    2: {
        "exchanges": [100, 101, 102, 105],
        "links": [(100, 101), (101, 102), (100, 105)],
    },
    3: {
        "exchanges": [100, 103, 104, 105],
        "links": [(100, 103), (100, 105), (103, 105), (103, 104)],
    },
    4: {
        "exchanges": [100, 102, 104],
        "links": [(100, 104), (102, 104)],
    },
    11: {
        "exchanges": [102, 105],
        "links": [(102, 105)],
    },
    12: {
        "exchanges": [101, 104],
        "links": [(101, 104)],
    },
}

MINI_INTERNET_STUB_AS_EXCHANGES = {
    150: 100,
    151: 100,
    152: 101,
    153: 101,
    154: 102,
    160: 103,
    161: 103,
    162: 103,
    163: 104,
    164: 104,
    170: 105,
    171: 105,
}

MINI_INTERNET_RS_PEERS = {
    100: [2, 3, 4],
    102: [2, 4],
    104: [3, 4],
    105: [2, 3],
}

MINI_INTERNET_PRIVATE_PEERINGS = (
    (100, [2], [150, 151]),
    (100, [3], [150]),
    (101, [2], [12]),
    (101, [12], [152, 153]),
    (102, [2, 4], [11, 154]),
    (102, [11], [154]),
    (103, [3], [160, 161, 162]),
    (104, [3, 4], [12]),
    (104, [4], [163]),
    (104, [12], [164]),
    (105, [3], [11, 170]),
    (105, [11], [171]),
)

GATEWAY_IX_BINDINGS = {
    "gw1": {"ix_id": 100, "ip_address": "10.100.0.250"},
    "gw2": {"ix_id": 101, "ip_address": "10.101.0.250"},
    "gw3": {"ix_id": 102, "ip_address": "10.102.0.250"},
}

GATEWAY_TRANSIT_PEERINGS = (
    (100, 2, SATELLITE_OPERATOR_ASN),
    (101, 2, SATELLITE_OPERATOR_ASN),
    (102, 4, SATELLITE_OPERATOR_ASN),
)

EXTERNAL_TEST_TARGETS = (
    {
        "asn": 154,
        "node_id": "as154-host-new",
        "ip_address": "10.154.0.129",
        "description": "Customized miniInternet host retained from B00 for UT reachability tests.",
    },
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the slot-based satellite prototype topology."
    )
    parser.add_argument(
        "--platform",
        choices=("amd", "arm"),
        default="amd",
        help="Target Docker platform.",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Compiler output directory.",
    )
    parser.add_argument(
        "--topology-json",
        default=str(DEFAULT_TOPOLOGY_JSON),
        help="Controller-visible topology inventory output path.",
    )
    return parser.parse_args()


def resolve_platform(name: str) -> Platform:
    if name == "amd":
        return Platform.AMD64
    if name == "arm":
        return Platform.ARM64
    raise ValueError(f"unsupported platform: {name}")


def resolve_ut_identity_address(ut_id: str) -> str:
    suffix = int(ut_id.removeprefix("ut"))
    return f"100.64.0.{suffix}"


def create_bootstrap_router(
    the_as,
    node_name: str,
    network_name: str,
    prefix: str,
    address: str,
):
    the_as.createNetwork(network_name, prefix)
    router = the_as.createRouter(node_name)
    router.joinNetwork(network_name, address)
    router.addSoftware("tcpdump")
    router.addSoftware("traceroute")
    return router


def create_ut_placeholder(
    satellite_as,
    ut_id: str,
    network_name: str,
    prefix: str,
    router_address: str,
    ut_address: str,
):
    satellite_as.createNetwork(network_name, prefix)
    bootstrap_router = satellite_as.createRouter(f"{ut_id}-placeholder-rtr")
    bootstrap_router.joinNetwork(network_name, router_address)

    ut = satellite_as.createHost(ut_id)
    ut.joinNetwork(network_name, ut_address)
    ut.appendStartCommand("ip route del default || true", isPostConfigCommand=True)
    ut.appendStartCommand("ip link add dummy0 type dummy", isPostConfigCommand=True)
    ut.appendStartCommand("ip link set dummy0 up", isPostConfigCommand=True)
    ut.appendStartCommand(
        f"ip addr add {resolve_ut_identity_address(ut_id)}/32 dev dummy0",
        isPostConfigCommand=True,
    )
    ut.addSoftware("curl")
    ut.addSoftware("tcpdump")
    ut.addSoftware("traceroute")
    return ut


def create_satellite_link(
    satellite_as,
    left_router,
    right_router,
    network_name: str,
    prefix: str,
    left_address: str,
    right_address: str,
) -> None:
    satellite_as.createNetwork(network_name, prefix)
    left_router.joinNetwork(network_name, left_address)
    right_router.joinNetwork(network_name, right_address)


def create_unique_transit_as(
    base: Base,
    *,
    asn: int,
    exchanges: List[int],
    intra_ix_links: List[Tuple[int, int]],
) -> None:
    transit_as = base.createAutonomousSystem(asn)
    routers: Dict[int, object] = {}

    for ix in exchanges:
        router_name = f"as{asn}-r{ix}"
        routers[ix] = transit_as.createRouter(router_name)
        routers[ix].joinNetwork(f"ix{ix}")

    for left_ix, right_ix in intra_ix_links:
        network_name = f"net_{left_ix}_{right_ix}"
        transit_as.createNetwork(network_name)
        routers[left_ix].joinNetwork(network_name)
        routers[right_ix].joinNetwork(network_name)


def create_unique_stub_as_with_hosts(
    base: Base,
    *,
    asn: int,
    exchange: int,
    hosts_total: int,
) -> None:
    stub_as = base.createAutonomousSystem(asn)
    stub_as.createNetwork("net0")

    router = stub_as.createRouter(f"as{asn}-router0")
    router.joinNetwork("net0")
    router.joinNetwork(f"ix{exchange}")

    for host_index in range(hosts_total):
        host = stub_as.createHost(f"as{asn}-host{host_index}")
        host.joinNetwork("net0")


def build_mini_internet(base: Base, ebgp: Ebgp) -> None:
    for ix_id, display_name in MINI_INTERNET_IX_DISPLAY_NAMES.items():
        ix = base.createInternetExchange(ix_id)
        ix.getPeeringLan().setDisplayName(display_name)

    for asn, layout in MINI_INTERNET_TRANSIT_AS_LAYOUT.items():
        create_unique_transit_as(
            base,
            asn=asn,
            exchanges=list(layout["exchanges"]),
            intra_ix_links=list(layout["links"]),
        )

    for asn, exchange in MINI_INTERNET_STUB_AS_EXCHANGES.items():
        create_unique_stub_as_with_hosts(
            base,
            asn=asn,
            exchange=exchange,
            hosts_total=MINI_INTERNET_HOSTS_PER_STUB_AS,
        )

    as154 = base.getAutonomousSystem(154)
    host_new = as154.createHost("as154-host-new")
    host_new.joinNetwork("net0", address="10.154.0.129")

    disable_rp_filter = OptionRegistry().sysctl_netipv4_conf_rp_filter(
        {"all": False, "default": False, "net0": False},
        mode=OptionMode.RUN_TIME,
    )
    host_new.setOption(disable_rp_filter)
    host_new.setOption(
        OptionRegistry().sysctl_netipv4_udp_rmem_min(
            5000,
            mode=OptionMode.RUN_TIME,
        )
    )

    for ix_id, peers in MINI_INTERNET_RS_PEERS.items():
        ebgp.addRsPeers(ix_id, peers)

    for ix_id, providers, customers in MINI_INTERNET_PRIVATE_PEERINGS:
        ebgp.addPrivatePeerings(
            ix_id,
            list(providers),
            list(customers),
            PeerRelationship.Provider,
        )


def build_emulator() -> Emulator:
    emu = Emulator()
    base = Base()
    routing = Routing()
    ebgp = Ebgp()
    ibgp = Ibgp()
    ospf = Ospf()

    build_mini_internet(base, ebgp)

    satellite_as = base.createAutonomousSystem(SATELLITE_ASN)
    operator_as = base.createAutonomousSystem(SATELLITE_OPERATOR_ASN)

    satellite_routers: Dict[str, object] = {}

    for satellite_id, subnet, address in (
        ("sat-a1", "10.250.1.0/24", "10.250.1.2"),
        ("sat-a2", "10.250.2.0/24", "10.250.2.2"),
        ("sat-a3", "10.250.3.0/24", "10.250.3.2"),
        ("sat-b1", "10.250.4.0/24", "10.250.4.2"),
        ("sat-b2", "10.250.5.0/24", "10.250.5.2"),
        ("sat-b3", "10.250.6.0/24", "10.250.6.2"),
    ):
        satellite_routers[satellite_id] = create_bootstrap_router(
            satellite_as,
            satellite_id,
            f"{satellite_id}-bootstrap",
            subnet,
            address,
        )

    gateway_routers: Dict[str, object] = {}
    for gateway_id, subnet, address in (
        ("gw1", "10.65.21.0/24", "10.65.21.2"),
        ("gw2", "10.65.22.0/24", "10.65.22.2"),
        ("gw3", "10.65.23.0/24", "10.65.23.2"),
    ):
        gateway_routers[gateway_id] = create_bootstrap_router(
            operator_as,
            gateway_id,
            f"{gateway_id}-bootstrap",
            subnet,
            address,
        )
        gateway_routers[gateway_id].joinNetwork(
            f"ix{GATEWAY_IX_BINDINGS[gateway_id]['ix_id']}",
            GATEWAY_IX_BINDINGS[gateway_id]["ip_address"],
        )

    create_ut_placeholder(
        satellite_as,
        "ut1",
        "ut1-placeholder",
        "10.250.101.0/30",
        "10.250.101.1",
        "10.250.101.2",
    )
    create_ut_placeholder(
        satellite_as,
        "ut2",
        "ut2-placeholder",
        "10.250.102.0/30",
        "10.250.102.1",
        "10.250.102.2",
    )
    create_ut_placeholder(
        satellite_as,
        "ut3",
        "ut3-placeholder",
        "10.250.103.0/30",
        "10.250.103.1",
        "10.250.103.2",
    )

    for left, right, label, prefix, left_ip, right_ip, _left_dir, _right_dir, _orbit in FIXED_INTRA_ORBIT_LINKS:
        create_satellite_link(
            satellite_as,
            satellite_routers[left],
            satellite_routers[right],
            f"link-{label}",
            prefix,
            left_ip,
            right_ip,
        )

    for ix_id, upstream_asn, gateway_asn in GATEWAY_TRANSIT_PEERINGS:
        ebgp.addPrivatePeering(
            ix_id,
            upstream_asn,
            gateway_asn,
            PeerRelationship.Provider,
        )

    ospf.maskAsn(SATELLITE_ASN)
    ospf.maskAsn(SATELLITE_OPERATOR_ASN)
    ibgp.maskAsn(SATELLITE_ASN)
    ibgp.maskAsn(SATELLITE_OPERATOR_ASN)

    emu.addLayer(base)
    emu.addLayer(routing)
    emu.addLayer(ebgp)
    emu.addLayer(ibgp)
    emu.addLayer(ospf)
    return emu


def _bootstrap_inventory(
    *,
    node_id: str,
    node_type: str,
    network_name: str,
    subnet: str,
    ip_address: str,
    purpose: str,
) -> Dict[str, object]:
    return {
        "network_name": network_name,
        "docker_network_name": network_name,
        "subnet": subnet,
        "purpose": purpose,
        "endpoint": {
            "node_id": node_id,
            "node_type": node_type,
            "network_name": network_name,
            "docker_network_name": network_name,
            "interface_name": network_name,
            "ip_address": ip_address,
        },
    }


def _endpoint_record(
    *,
    node_id: str,
    node_type: str,
    interface_name: str,
    ip_address: str,
    logical_direction: str | None = None,
) -> Dict[str, object]:
    record: Dict[str, object] = {
        "node_id": node_id,
        "node_type": node_type,
        "interface_name": interface_name,
        "ip_address": ip_address,
    }
    if logical_direction is not None:
        record["logical_direction"] = logical_direction
    return record


def build_slot_inventory() -> Dict[str, List[Dict[str, object]]]:
    ut_slots: List[Dict[str, object]] = []
    for ut_index, ut_id in enumerate(USER_TERMINALS, start=1):
        for slot_index in range(ACCESS_SLOT_COUNT):
            third_octet = ut_index * 10 + slot_index + 1
            interface_name = f"ua-u{ut_index}s{slot_index}"
            ut_slots.append(
                {
                    "slot_name": f"{ut_id}-access-slot{slot_index}",
                    "slot_type": "ut_access",
                    "network_name": f"{ut_id}-access-slot{slot_index}",
                    "docker_network_name": f"{ut_id}-access-slot{slot_index}",
                    "subnet": f"10.90.{third_octet}.0/29",
                    "bridge_gateway_ip": f"10.90.{third_octet}.6",
                    "ut_id": ut_id,
                    "slot_index": slot_index,
                    "candidate_satellite_ids": list(SATELLITES),
                    "preconnected_endpoint": _endpoint_record(
                        node_id=ut_id,
                        node_type="user_terminal",
                        interface_name=interface_name,
                        ip_address=f"10.90.{third_octet}.2/29",
                    ),
                    "dynamic_endpoint_template": _endpoint_record(
                        node_id="<runtime-satellite>",
                        node_type="satellite",
                        interface_name=interface_name,
                        ip_address=f"10.90.{third_octet}.1/29",
                    ),
                }
            )

    gateway_slots: List[Dict[str, object]] = []
    for gw_index, gateway_id in enumerate(GATEWAYS, start=1):
        for slot_index in range(GATEWAY_SLOT_COUNT):
            third_octet = gw_index * 10 + slot_index + 1
            interface_name = f"ga-g{gw_index}s{slot_index}"
            gateway_slots.append(
                {
                    "slot_name": f"{gateway_id}-gateway-slot{slot_index}",
                    "slot_type": "gw_access",
                    "network_name": f"{gateway_id}-gateway-slot{slot_index}",
                    "docker_network_name": f"{gateway_id}-gateway-slot{slot_index}",
                    "subnet": f"10.91.{third_octet}.0/29",
                    "bridge_gateway_ip": f"10.91.{third_octet}.6",
                    "gateway_id": gateway_id,
                    "slot_index": slot_index,
                    "candidate_satellite_ids": list(SATELLITES),
                    "preconnected_endpoint": _endpoint_record(
                        node_id=gateway_id,
                        node_type="gateway",
                        interface_name=interface_name,
                        ip_address=f"10.91.{third_octet}.2/29",
                    ),
                    "dynamic_endpoint_template": _endpoint_record(
                        node_id="<runtime-satellite>",
                        node_type="satellite",
                        interface_name=interface_name,
                        ip_address=f"10.91.{third_octet}.1/29",
                    ),
                }
            )

    cross_plane_slots: List[Dict[str, object]] = []
    for slot_index in range(CROSS_PLANE_SLOT_COUNT):
        third_octet = slot_index + 1
        cross_plane_slots.append(
            {
                "slot_name": f"ab-cross-slot{slot_index}",
                "slot_type": "cross_plane_isl",
                "network_name": f"ab-cross-slot{slot_index}",
                "docker_network_name": f"ab-cross-slot{slot_index}",
                "subnet": f"10.92.{third_octet}.0/29",
                "bridge_gateway_ip": f"10.92.{third_octet}.6",
                "slot_index": slot_index,
                "orbit_pair": ["orbit-a", "orbit-b"],
                "orbit_a_satellite_ids": list(ORBIT_A_SATELLITES),
                "orbit_b_satellite_ids": list(ORBIT_B_SATELLITES),
                "endpoint_a_template": _endpoint_record(
                    node_id="<runtime-orbit-a-satellite>",
                    node_type="satellite",
                    interface_name=f"xp{slot_index}a",
                    ip_address=f"10.92.{third_octet}.1/29",
                ),
                "endpoint_b_template": _endpoint_record(
                    node_id="<runtime-orbit-b-satellite>",
                    node_type="satellite",
                    interface_name=f"xp{slot_index}b",
                    ip_address=f"10.92.{third_octet}.2/29",
                ),
            }
        )

    return {
        "ut_access": ut_slots,
        "gateway_access": gateway_slots,
        "cross_plane_isl": cross_plane_slots,
    }


def emit_topology_inventory(path: Path) -> None:
    fixed_links: List[Dict[str, object]] = []
    for left, right, label, prefix, left_ip, right_ip, left_dir, right_dir, orbit_scope in FIXED_INTRA_ORBIT_LINKS:
        network_name = f"link-{label}"
        fixed_links.append(
            {
                "link_id": f"fixed-{label}",
                "link_type": "satellite_satellite_fixed_intra_orbit",
                "network_name": network_name,
                "docker_network_name": network_name,
                "subnet": prefix,
                "orbit_scope": orbit_scope,
                "endpoints": [
                    {
                        **_endpoint_record(
                            node_id=left,
                            node_type="satellite",
                            interface_name=network_name,
                            ip_address=left_ip,
                            logical_direction=left_dir,
                        ),
                        "network_name": network_name,
                        "docker_network_name": network_name,
                    },
                    {
                        **_endpoint_record(
                            node_id=right,
                            node_type="satellite",
                            interface_name=network_name,
                            ip_address=right_ip,
                            logical_direction=right_dir,
                        ),
                        "network_name": network_name,
                        "docker_network_name": network_name,
                    },
                ],
            }
        )

    topology = {
        "schema_version": "satellite-phase2-slot-topology-v1",
        "description": (
            "Controller-visible static topology inventory for the slot-based "
            "satellite dynamic-link prototype."
        ),
        "logical_port_labels": ["front", "back", "left", "right"],
        "prototype_scope": {
            "satellites": len(SATELLITES),
            "gateways": len(GATEWAYS),
            "user_terminals": len(USER_TERMINALS),
            "access_slots_per_ut": ACCESS_SLOT_COUNT,
            "gateway_slots_per_gw": GATEWAY_SLOT_COUNT,
            "cross_plane_slot_count": CROSS_PLANE_SLOT_COUNT,
            "internet_exchanges": len(MINI_INTERNET_IX_DISPLAY_NAMES),
            "mini_internet_transit_ases": len(MINI_INTERNET_TRANSIT_AS_LAYOUT),
            "mini_internet_stub_ases": len(MINI_INTERNET_STUB_AS_EXCHANGES),
        },
        "internet_exchanges": [
            {
                "ix_id": ix_id,
                "network_name": f"ix{ix_id}",
                "display_name": display_name,
            }
            for ix_id, display_name in sorted(MINI_INTERNET_IX_DISPLAY_NAMES.items())
        ],
        "satellites": [
            {
                "satellite_id": satellite_id,
                "orbit_id": "orbit-a" if satellite_id.startswith("sat-a") else "orbit-b",
                "logical_ports": {"front": None, "back": None, "left": None, "right": None},
                "bootstrap_network": _bootstrap_inventory(
                    node_id=satellite_id,
                    node_type="satellite",
                    network_name=f"{satellite_id}-bootstrap",
                    subnet=f"10.250.{index}.0/24",
                    ip_address=f"10.250.{index}.2/24",
                    purpose="compile_time_carrier",
                ),
            }
            for index, satellite_id in enumerate(SATELLITES, start=1)
        ],
        "gateways": [
            {
                "gateway_id": gateway_id,
                "role": "satellite_operator_border_gateway",
                "asn": SATELLITE_OPERATOR_ASN,
                "bootstrap_network": _bootstrap_inventory(
                    node_id=gateway_id,
                    node_type="gateway",
                    network_name=f"{gateway_id}-bootstrap",
                    subnet=f"10.65.{20 + index}.0/24",
                    ip_address=f"10.65.{20 + index}.2/24",
                    purpose="compile_time_carrier",
                ),
                "internet_uplink": {
                    "ix_id": GATEWAY_IX_BINDINGS[gateway_id]["ix_id"],
                    "network_name": f"ix{GATEWAY_IX_BINDINGS[gateway_id]['ix_id']}",
                    "ip_address": GATEWAY_IX_BINDINGS[gateway_id]["ip_address"],
                    "peering_asn": SATELLITE_OPERATOR_ASN,
                },
            }
            for index, gateway_id in enumerate(GATEWAYS, start=1)
        ],
        "user_terminals": [
            {
                "ut_id": ut_id,
                "identity_interface": {
                    "if_name": "dummy0",
                    "ip_address": f"{resolve_ut_identity_address(ut_id)}/32",
                    "semantics": "stable_internal_identity_not_publicly_advertised",
                },
                "bootstrap_network": _bootstrap_inventory(
                    node_id=ut_id,
                    node_type="user_terminal",
                    network_name=f"{ut_id}-placeholder",
                    subnet=f"10.250.10{index}.0/30",
                    ip_address=f"10.250.10{index}.2/30",
                    purpose="compile_time_carrier_only",
                ),
            }
            for index, ut_id in enumerate(USER_TERMINALS, start=1)
        ],
        "fixed_links": {
            "satellite_satellite": fixed_links,
        },
        "mini_internet": {
            "transit_ases": [
                {
                    "asn": asn,
                    "exchange_ids": list(layout["exchanges"]),
                    "internal_links": list(layout["links"]),
                }
                for asn, layout in sorted(MINI_INTERNET_TRANSIT_AS_LAYOUT.items())
            ],
            "stub_ases": [
                {
                    "asn": asn,
                    "exchange_id": exchange,
                    "host_node_ids": [
                        f"as{asn}-host{host_index}"
                        for host_index in range(MINI_INTERNET_HOSTS_PER_STUB_AS)
                    ] + (["as154-host-new"] if asn == 154 else []),
                }
                for asn, exchange in sorted(MINI_INTERNET_STUB_AS_EXCHANGES.items())
            ],
            "external_test_targets": list(EXTERNAL_TEST_TARGETS),
            "gateway_ix_peerings": [
                {
                    "gateway_id": gateway_id,
                    "ix_id": GATEWAY_IX_BINDINGS[gateway_id]["ix_id"],
                    "ip_address": GATEWAY_IX_BINDINGS[gateway_id]["ip_address"],
                }
                for gateway_id in GATEWAYS
            ],
        },
        "slot_inventory": build_slot_inventory(),
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(topology, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    emu = build_emulator()
    output_dir = Path(args.output).resolve()
    topology_json = Path(args.topology_json).resolve()

    emu.render()
    emit_topology_inventory(topology_json)
    emu.compile(
        Docker(
            selfManagedNetwork=True,
            platform=resolve_platform(args.platform),
            namingScheme="{name}",
        ),
        str(output_dir),
        override=True,
    )

    print("Slot-based satellite topology compiled successfully.")
    print(f"Output directory: {output_dir}")
    print(f"Topology inventory: {topology_json}")
    print(f"Satellites: {', '.join(SATELLITES)}")
    print(f"Gateways/AS{SATELLITE_OPERATOR_ASN}: {', '.join(GATEWAYS)}")
    print(f"User terminals: {', '.join(USER_TERMINALS)}")
    print(
        "MiniInternet: "
        f"ix={len(MINI_INTERNET_IX_DISPLAY_NAMES)}, "
        f"transit_as={len(MINI_INTERNET_TRANSIT_AS_LAYOUT)}, "
        f"stub_as={len(MINI_INTERNET_STUB_AS_EXCHANGES)}"
    )
    print(
        "Slot inventory: "
        f"ut_access={len(USER_TERMINALS) * ACCESS_SLOT_COUNT}, "
        f"gateway_access={len(GATEWAYS) * GATEWAY_SLOT_COUNT}, "
        f"cross_plane_isl={CROSS_PLANE_SLOT_COUNT}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
