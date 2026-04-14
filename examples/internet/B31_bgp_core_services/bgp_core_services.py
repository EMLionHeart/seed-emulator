#!/usr/bin/env python3
# encoding: utf-8

import argparse
from pathlib import Path

import yaml

from seedemu.compiler import Docker, Platform
from seedemu.core import Emulator
from seedemu.layers import Base, Ebgp, Ibgp, Ospf, Routing, PeerRelationship
from seedemu.services import AliceLGService, BGPControlService, BGPObservationService
from seedemu.utilities import Makers


DEFAULT_CONTROL_HOST_PORT = 18081
DEFAULT_OBSERVATION_HOST_PORT = "29184:29184"


def build_emulator():
    emu = Emulator()
    base = Base()
    ebgp = Ebgp()

    ix100 = base.createInternetExchange(100)
    ix101 = base.createInternetExchange(101)
    ix100.getPeeringLan().setDisplayName("NYC-100")
    ix101.getPeeringLan().setDisplayName("Chicago-101")

    transit_as = Makers.makeTransitAs(base, 2, [100, 101], [(100, 101)])
    Makers.makeStubAsWithHosts(emu, base, 150, 100, 1)
    Makers.makeStubAsWithHosts(emu, base, 151, 101, 1)

    ebgp.addPrivatePeerings(100, [2], [150], PeerRelationship.Provider)
    ebgp.addPrivatePeerings(101, [2], [151], PeerRelationship.Provider)

    emu.addLayer(base)
    emu.addLayer(Routing())
    emu.addLayer(ebgp)
    emu.addLayer(Ibgp())
    emu.addLayer(Ospf())

    return emu, transit_as


def attach_services(emu: Emulator, transit_as, *, enable_alice: bool = False):
    control = BGPControlService()
    control.attachRouter(
        transit_as,
        "r100",
        exposure_mode="host",
        host_port=DEFAULT_CONTROL_HOST_PORT,
    )

    observation = BGPObservationService()
    observation.observeRouter(transit_as, "r100")

    alice = None
    if enable_alice:
        alice = AliceLGService()
        alice.attachObservation(observation)

    emu.addLayer(control)
    emu.addLayer(observation)
    if alice is not None:
        emu.addLayer(alice)

    return control, observation, alice


def resolve_platform(name: str) -> Platform:
    if name == "amd":
        return Platform.AMD64
    if name == "arm":
        return Platform.ARM64
    raise ValueError(f"unsupported platform: {name}")


def parse_args():
    parser = argparse.ArgumentParser(description="Core BGP services example")
    parser.add_argument(
        "--mode",
        choices=["core", "full"],
        default="core",
        help="Current phase implements only the backend-only core mode.",
    )
    parser.add_argument(
        "--platform",
        choices=["amd", "arm"],
        default="amd",
        help="Target Docker platform.",
    )
    parser.add_argument(
        "--output",
        default=str(Path(__file__).with_name("output")),
        help="Compiler output directory.",
    )
    return parser.parse_args()


def print_endpoints(control: BGPControlService, observation: BGPObservationService):
    print("BGP control endpoints:")
    for endpoint in control.getEndpoints():
        print(f"  AS{endpoint['asn']} {endpoint['router']}: {endpoint['endpoint']}")
        print(f"    exposure_mode={endpoint['exposure_mode']} host_port={endpoint['host_port']}")

    print("BGP observation endpoints:")
    for endpoint in observation.getEndpoints():
        print(f"  AS{endpoint['asn']} {endpoint['router']}: {endpoint['endpoint']}")
        print(f"    exposure_mode={endpoint['exposure_mode']} host_port={endpoint['host_port']}")


def get_full_mode_output_callbacks():
    def patch_compose(_compiler) -> None:
        compose_path = Path("output") / "docker-compose.yml"
        compose = yaml.safe_load(compose_path.read_text(encoding="utf-8")) or {}
        services = compose.setdefault("services", {})
        router = services["brdnode_2_r100"]
        ports = [str(port) for port in router.get("ports", [])]
        if DEFAULT_OBSERVATION_HOST_PORT not in ports:
            ports.append(DEFAULT_OBSERVATION_HOST_PORT)
        router["ports"] = ports
        compose_path.write_text(
            yaml.safe_dump(compose, sort_keys=False),
            encoding="utf-8",
        )

    return [patch_compose]


def main():
    args = parse_args()

    emu, transit_as = build_emulator()
    control, observation, alice = attach_services(
        emu,
        transit_as,
        enable_alice=args.mode == "full",
    )

    emu.render()
    print_endpoints(control, observation)

    docker = Docker(platform=resolve_platform(args.platform))
    emu.compile(docker, args.output, override=True)

    if alice is not None:
        emu.updateOutputDirectory(docker, alice.get_output_callbacks())
        emu.updateOutputDirectory(docker, get_full_mode_output_callbacks())


if __name__ == "__main__":
    main()
