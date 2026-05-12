#!/usr/bin/env python3
# encoding: utf-8

"""Serial runtime handover demo for the slot-based satellite prototype."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Sequence


DEFAULT_DIR = Path(__file__).resolve().parent
DEFAULT_CONTROLLER = DEFAULT_DIR / "controller.py"
DEFAULT_LINK_STATE = DEFAULT_DIR / "configs" / "link_state.json"


STATE_CATALOG: Dict[str, Dict[str, Any]] = {
    "t0": {
        "description": "ut1->sat-a1, gw1->sat-b1, cross-plane sat-a1<->sat-b1",
        "slot_reuse_note": (
            "demo reuses ut1-access-slot0, gw1-gateway-slot0, and ab-cross-slot0; "
            "probe IPs stay constant while binding changes."
        ),
        "steps": [
            {
                "label": "attach ut1 to sat-a1",
                "args": ["--attach-ut", "ut1:sat-a1", "--slot-name", "ut1-access-slot0"],
            },
            {
                "label": "attach gw1 to sat-b1",
                "args": ["--attach-gw", "gw1:sat-b1", "--slot-name", "gw1-gateway-slot0"],
            },
            {
                "label": "connect cross-plane a1-b1",
                "args": [
                    "--connect-cross",
                    "sat-a1:sat-b1:left:right",
                    "--slot-name",
                    "ab-cross-slot0",
                ],
            },
        ],
        "ut_ping_targets": [
            {"label": "ut1 current access slot peer", "ip": "10.90.11.1"},
        ],
        "gw_ping_targets": [
            {"label": "gw1 current feeder slot peer", "ip": "10.91.11.1"},
        ],
        "cross_ping": {
            "source_node": "sat-a1",
            "label": "sat-a1 current cross-plane peer",
            "ip": "10.92.1.2",
        },
    },
    "t1": {
        "description": "ut1->sat-a2, gw1->sat-b2, cross-plane sat-a2<->sat-b2",
        "slot_reuse_note": (
            "demo reuses ut1-access-slot0, gw1-gateway-slot0, and ab-cross-slot0; "
            "probe IPs stay constant while binding changes."
        ),
        "steps": [
            {
                "label": "release cross-plane a1-b1",
                "args": ["--release-slot", "ab-cross-slot0"],
            },
            {
                "label": "detach ut1 from sat-a1",
                "args": ["--detach-ut", "ut1", "--slot-name", "ut1-access-slot0"],
            },
            {
                "label": "detach gw1 from sat-b1",
                "args": ["--detach-gw", "gw1", "--slot-name", "gw1-gateway-slot0"],
            },
            {
                "label": "attach ut1 to sat-a2",
                "args": ["--attach-ut", "ut1:sat-a2", "--slot-name", "ut1-access-slot0"],
            },
            {
                "label": "attach gw1 to sat-b2",
                "args": ["--attach-gw", "gw1:sat-b2", "--slot-name", "gw1-gateway-slot0"],
            },
            {
                "label": "connect cross-plane a2-b2",
                "args": [
                    "--connect-cross",
                    "sat-a2:sat-b2:left:right",
                    "--slot-name",
                    "ab-cross-slot0",
                ],
            },
        ],
        "ut_ping_targets": [
            {"label": "ut1 current access slot peer", "ip": "10.90.11.1"},
        ],
        "gw_ping_targets": [
            {"label": "gw1 current feeder slot peer", "ip": "10.91.11.1"},
        ],
        "cross_ping": {
            "source_node": "sat-a2",
            "label": "sat-a2 current cross-plane peer",
            "ip": "10.92.1.2",
        },
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Serial runtime handover demo for the slot-based satellite prototype."
    )
    parser.add_argument(
        "--controller",
        default=str(DEFAULT_CONTROLLER),
        help="Path to controller.py.",
    )
    parser.add_argument(
        "--link-state",
        default=str(DEFAULT_LINK_STATE),
        help="Path to link_state.json.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Seconds to wait after each full state application.",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=0,
        help="Number of state transitions to run. 0 means loop forever.",
    )
    parser.add_argument(
        "--start-state",
        choices=tuple(STATE_CATALOG.keys()),
        default="t0",
        help="Initial runtime state to apply.",
    )
    return parser.parse_args()


def load_link_state(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def occupied_dynamic_slots(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        entry
        for entry in state.get("slots", [])
        if entry.get("slot_type") != "fixed_isl" and entry.get("occupied", False)
    ]


def active_link_summaries(state: Dict[str, Any]) -> List[str]:
    summaries: List[str] = []
    for entry in state.get("slots", []):
        slot_type = str(entry.get("slot_type"))
        if slot_type == "fixed_isl":
            binding = entry.get("binding") or {}
            summaries.append(
                f"{entry.get('slot_name')} fixed "
                f"{binding.get('satellite_a_id')}<->{binding.get('satellite_b_id')}"
            )
            continue
        if not entry.get("occupied", False):
            continue
        binding = entry.get("binding") or {}
        if slot_type == "ut_access":
            summaries.append(
                f"{entry.get('slot_name')} ut "
                f"{binding.get('ut_id')}->{binding.get('satellite_id')}"
            )
        elif slot_type == "gw_access":
            summaries.append(
                f"{entry.get('slot_name')} gw "
                f"{binding.get('gateway_id')}->{binding.get('satellite_id')}"
            )
        elif slot_type == "cross_plane_isl":
            summaries.append(
                f"{entry.get('slot_name')} cross "
                f"{binding.get('satellite_a_id')}({binding.get('satellite_a_direction')})"
                f"<->{binding.get('satellite_b_id')}({binding.get('satellite_b_direction')})"
            )
    return summaries


def link_state_summary(state: Dict[str, Any]) -> str:
    slots = state.get("slots", [])
    occupied = occupied_dynamic_slots(state)
    occupied_by_type = {
        "ut_access": 0,
        "gw_access": 0,
        "cross_plane_isl": 0,
        "fixed_isl": 0,
    }
    for entry in slots:
        slot_type = str(entry.get("slot_type"))
        if slot_type == "fixed_isl":
            occupied_by_type["fixed_isl"] += 1
        elif entry.get("occupied", False):
            occupied_by_type[slot_type] = occupied_by_type.get(slot_type, 0) + 1
    return (
        f"dynamic_occupied={len(occupied)} "
        f"(ut={occupied_by_type['ut_access']}, "
        f"gw={occupied_by_type['gw_access']}, "
        f"cross={occupied_by_type['cross_plane_isl']}), "
        f"fixed={occupied_by_type['fixed_isl']}"
    )


def run_command(command: Sequence[str], *, label: str) -> None:
    print(f"[demo] action: {label}")
    print(f"[demo] command: {' '.join(command)}")
    result = subprocess.run(
        list(command),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.stdout.strip():
        print(result.stdout.rstrip())
    if result.stderr.strip():
        print(result.stderr.rstrip())
    if result.returncode != 0:
        raise RuntimeError(f"{label} failed with exit status {result.returncode}")


def ping_once(node_id: str, target_ip: str) -> bool:
    result = subprocess.run(
        ["docker", "exec", node_id, "ping", "-c", "1", "-W", "1", target_ip],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def print_connectivity_probes(state_name: str, state_spec: Dict[str, Any]) -> None:
    print(f"[demo] connectivity probes for {state_name}")
    for probe in state_spec.get("ut_ping_targets", []):
        ok = ping_once("ut1", str(probe["ip"]))
        print(f"  ut1 probe {probe['label']} {probe['ip']}: {'up' if ok else 'down'}")
    for probe in state_spec.get("gw_ping_targets", []):
        ok = ping_once("gw1", str(probe["ip"]))
        print(f"  gw1 probe {probe['label']} {probe['ip']}: {'up' if ok else 'down'}")
    cross_ping = state_spec.get("cross_ping")
    if isinstance(cross_ping, dict):
        ok = ping_once(str(cross_ping["source_node"]), str(cross_ping["ip"]))
        print(
            f"  cross probe {cross_ping['label']} {cross_ping['ip']}: "
            f"{'up' if ok else 'down'}"
        )


def print_runtime_snapshot(link_state_path: Path, state_name: str) -> None:
    state = load_link_state(link_state_path)
    active_links = active_link_summaries(state)
    occupied_slots = [
        str(entry.get("slot_name")) for entry in occupied_dynamic_slots(state)
    ]
    print(f"[demo] runtime snapshot after {state_name}")
    print("[demo] active links:")
    for summary in active_links:
        print(f"  {summary}")
    print("[demo] occupied slots:")
    if occupied_slots:
        for slot_name in occupied_slots:
            print(f"  {slot_name}")
    else:
        print("  (none)")
    print(f"[demo] link_state summary: {link_state_summary(state)}")
    print_connectivity_probes(state_name, STATE_CATALOG[state_name])


def controller_command(controller_path: Path, *args: str) -> List[str]:
    return [sys.executable, str(controller_path), *args]


def apply_state(
    *,
    controller_path: Path,
    link_state_path: Path,
    state_name: str,
) -> None:
    state_spec = STATE_CATALOG[state_name]
    print(f"[demo] applying {state_name}: {state_spec['description']}")
    slot_reuse_note = state_spec.get("slot_reuse_note")
    if isinstance(slot_reuse_note, str) and slot_reuse_note:
        print(f"[demo] note: {slot_reuse_note}")
    for step in state_spec.get("steps", []):
        run_command(
            controller_command(controller_path, *step["args"]),
            label=str(step["label"]),
        )
        print_runtime_snapshot(link_state_path, state_name)


def next_state(current_state: str) -> str:
    return "t1" if current_state == "t0" else "t0"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(line_buffering=True)
    args = parse_args()
    controller_path = Path(args.controller).resolve()
    link_state_path = Path(args.link_state).resolve()

    if not controller_path.exists():
        raise SystemExit(f"controller not found: {controller_path}")
    if not link_state_path.exists():
        raise SystemExit(f"link state not found: {link_state_path}")

    current_link_state = load_link_state(link_state_path)
    if not current_link_state.get("runtime_initialized", False):
        raise SystemExit("slot runtime is not initialized; run --init-slot-runtime first")

    current_state = args.start_state
    transition_count = 0
    try:
        while True:
            apply_state(
                controller_path=controller_path,
                link_state_path=link_state_path,
                state_name=current_state,
            )
            transition_count += 1
            print(
                f"[demo] state {current_state} applied; sleeping {args.interval:.1f}s before next transition"
            )
            time.sleep(args.interval)
            if args.iterations and transition_count >= args.iterations:
                break
            current_state = next_state(current_state)
    except KeyboardInterrupt:
        print("[demo] interrupted by user")
        return 130
    except RuntimeError as error:
        print(f"[demo] stopped due to error: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
