# Satellite Phase 2 Slot-Based Dynamic Link Design

Chinese version:
[satellite_phase1_design.zh.md](/home/seed/seed-emulator/examples/satellite/docs/satellite_phase1_design.zh.md:1)

This document describes the current mainline design for the satellite example.
The old candidate-link / snapshot-activation mainline is retired for this
prototype stage.

## Scope

- keep the prototype inside `examples/satellite/`
- no SEED core changes by default
- keep the scale fixed at 3 `UT`, 6 `ST`, 3 `GW`
- restore the full `B00 miniInternet` as the ground-side Internet structure
- validate slot-based runtime link management
- do not require real `MPLS`, large scale, or a custom frontend

This phase explicitly does not do the following:

- it does not require `UT` to ping through to the `miniInternet`
- it does not add temporary `route/NAT`
- it does not restore the old `forwarding_state`, `candidate`, or `snapshot` workflows
- it does not introduce real `MPLS`
- it does not run `OSPF/iBGP/eBGP` on the satellite runtime side

## Runtime Boundary

- SEED creates base containers, fixed intra-plane links, and the compile-time `miniInternet`
- `controller.py` owns runtime slot bridge creation and runtime connect/disconnect
- all runtime state is explicit in `link_state.json`
- no hidden background controller logic
- `link_state.json` remains limited to satellite slot runtime plus `fixed_isl`
- `miniInternet` internal links are not mirrored into `link_state.json`

## UT Address Semantics

The current prototype distinguishes two different address roles:

- `UT stable identity address/prefix`
  - `ut1 = 100.64.0.1/32`
  - `ut2 = 100.64.0.2/32`
  - `ut3 = 100.64.0.3/32`
  - these addresses live on `dummy0` and remain unchanged across `UT-ST` attach/detach
  - future forwarding/MPLS work should treat them as service endpoints, `FEC` targets, and user identity
- `UT-ST runtime slot link address`
  - for example `10.90.x.x/29`
  - these addresses describe only the current access-link next hop
  - slot-link IPs must not be treated as user identity addresses

## Routing Boundary

- the restored `miniInternet` keeps SEED-managed `Routing + Ebgp + Ibgp + Ospf`
- `GW` ground-side IX uplinks keep SEED-managed `eBGP`
- `AS250` and the satellite-runtime side of `AS65000` are masked from SEED `Ospf/Ibgp`
- `UT-ST`, `GW-ST`, cross-plane `ST-ST`, and the current fixed intra-plane `ISL` remain controller-owned runtime topology semantics

## Link Model

### `UT-ST`

- each `UT` owns 2 access slots
- each slot is one Docker bridge network
- initialization connects only the `UT` side
- runtime attach connects or disconnects one `ST`
- if no free slot exists, the controller must fail clearly

### `GW-ST`

- each `GW` owns 2 gateway slots
- each slot is one Docker bridge network
- initialization connects only the `GW` side
- runtime attach connects or disconnects one `ST`
- if no free slot exists, the controller must fail clearly

### Fixed Intra-Plane `ISL`

- `sat-a1 <-> sat-a2 <-> sat-a3`
- `sat-b1 <-> sat-b2 <-> sat-b3`
- these links remain compile-time fixed p2p links
- they appear in `link_state.json` as `fixed_isl`

### Cross-Plane `ISL`

- orbit-a and orbit-b share 3 reusable cross-plane slots
- each slot is one shared Docker bridge network
- initialization creates the bridge only
- runtime connect attaches two `ST` endpoints to the same bridge
- disconnect removes both endpoints and returns the slot to free state

## State Model

`link_state.json` is the controller-owned runtime state file.

Each slot entry records:

- `slot_name`
- `slot_type`
- `network_name`
- `docker_network_name`
- `subnet`
- `bridge_gateway_ip`
- `preconnected_endpoints`
- `dynamic_endpoints`
- `occupied`
- `binding`

For cross-plane slots, `binding` records:

- `satellite_a_id`
- `satellite_b_id`
- `satellite_a_direction`
- `satellite_b_direction`

For fixed intra-plane links, `slot_type=fixed_isl` and `occupied=true`.

## Safety Rules

- after each `docker network connect`, the target container must gain exactly 1 new interface
- if the delta is not exactly 1, abort immediately
- only rename the discovered new interface
- do not rename any uncertain existing interface
- do not write `link_state.json` on failure
- if cross-plane second-end connect fails, roll back the first end

## Current CLI Surface

Initialize all runtime slot bridges and fixed-side preconnects:

```bash
python3 examples/satellite/controller.py --init-slot-runtime
```

Show runtime state:

```bash
python3 examples/satellite/controller.py --show-link-state
```

Attach and detach `UT`:

```bash
python3 examples/satellite/controller.py --attach-ut ut1:sat-a1
python3 examples/satellite/controller.py --attach-ut ut1:sat-a1 --slot-name ut1-access-slot1
python3 examples/satellite/controller.py --detach-ut ut1
```

Attach and detach `GW`:

```bash
python3 examples/satellite/controller.py --attach-gw gw1:sat-b1
python3 examples/satellite/controller.py --attach-gw gw1:sat-b1 --slot-name gw1-gateway-slot1
python3 examples/satellite/controller.py --detach-gw gw1
```

Connect and release cross-plane `ISL`:

```bash
python3 examples/satellite/controller.py --connect-cross sat-a1:sat-b1:left:right
python3 examples/satellite/controller.py --connect-cross sat-a1:sat-b1:left:right --slot-name ab-cross-slot2
python3 examples/satellite/controller.py --release-slot ab-cross-slot0
```

If `--slot-name` is not provided for attach/connect operations, the controller
uses the first free slot in sorted slot-name order.

## Bootstrap Interface Caveat

- some containers may still keep SEED bootstrap-era `eth*` interfaces in `DOWN` state
- this is not treated as a slot-runtime bug by itself
- the controller validates slot safety by comparing interface sets immediately before and after `docker network connect`
- the success condition is “exactly one newly appeared interface”, not “no old bootstrap `eth*` remain”

## Validation Focus

The current prototype is considered successful if it demonstrates:

- bounded slot resources
- runtime bridge reuse
- controller-visible state consistency
- safe rollback on partial failure
- easy inspection from Docker and `link_state.json`

## Runtime Demo Loop

The current demo loop is intentionally small and serial:

- `t0`
  - `ut1 -> sat-a1`
  - `gw1 -> sat-b1`
  - cross-plane `sat-a1 <-> sat-b1`
- `t1`
  - release cross-plane
  - detach `ut1`
  - detach `gw1`
  - `ut1 -> sat-a2`
  - `gw1 -> sat-b2`
  - cross-plane `sat-a2 <-> sat-b2`

The demo driver is:

`examples/satellite/demo_runtime_handover.py`

It runs serially only and prints:

- current active links
- current occupied slots
- current runtime action
- `link_state.json` summary
- simple current-state reachability probes

The demo intentionally reuses the same slot names:

- `ut1-access-slot0`
- `gw1-gateway-slot0`
- `ab-cross-slot0`

So the probe IPs are slot-peer IPs rather than satellite-specific
access/feeder/cross-plane IPs. The main changes during handover are the
`binding`, container interfaces, and transient reachability, not the probe IPs
themselves.

This current demo only validates the slot-runtime link behavior and state
consistency. It does not claim that `UT -> miniInternet` forwarding is already
implemented, and it does not add temporary `route/NAT` or restore the old
forwarding baseline.

## Map Observation Boundary

- `SEED map` for this example is available at `http://127.0.0.1:8080/pro/map`
- for this phase, treat it as a compile-time topology visualization
- that compile-time view should now include the restored `B00 miniInternet`, the `AS250` satellite side, and the `AS65000` gateways
- do not rely on it as a real-time rendering of runtime slot bridge connect/disconnect
- runtime truth is still the controller output, container interface state, and `link_state.json`
- because forwarding/MPLS is intentionally out of scope here, there is no stable `UT -> ground host` end-to-end probe target yet
- the recommended observation style is:
  - keep pinging `10.90.11.1` from `UT` and watch `ut1-access-slot0` `binding.satellite_id`
  - keep pinging `10.91.11.1` from `GW` and watch `gw1-gateway-slot0` `binding.satellite_id`
  - watch transient down/up of cross-plane peer `10.92.1.2` from `sat-a1` or `sat-a2` while reading the `ab-cross-slot0` binding
- the current demo is meant to highlight:
  - runtime slot stability
  - dynamic link visualization
  - rollback safety
  - slot reuse
  - Docker network/interface cleanup

## Future Forwarding/MPLS Demo Plan

The following state design is the planned future loop for the next
forwarding/MPLS baseline stage. It is a plan only; it does not mean that
`UT -> miniInternet` reachability is implemented in the current phase.

`t0`:

- `ut1 -> sat-a1`
- `ut2 -> sat-a2`
- `gw1 -> sat-b1`
- `gw2 -> sat-a3`
- `ab-cross-slot0: sat-a1(left) <-> sat-b1(right)`
- `ab-cross-slot1: sat-a2(left) <-> sat-b2(right)`

`t1`:

- `ut1 -> sat-a2`
- `ut2 -> sat-a3`
- `gw1 release`
- `gw2 -> sat-b2`
- `ab-cross-slot0: sat-a2(left) <-> sat-b2(right)`
- `ab-cross-slot1: sat-a3(left) <-> sat-b3(right)`

The next forwarding/MPLS stage is expected to keep the `UT stable identity
address/prefix` reachable across these runtime link-state changes while using
slot-link addresses only as current next-hop information.
