# Satellite Slot-Based Dynamic Link Prototype

Chinese version:
[README.zh.md](/home/seed/seed-emulator/examples/satellite/README.zh.md:1)

This directory contains the current mainline satellite prototype for SEED
Emulator. The prototype is intentionally small and controller-driven:

- 3 `UT`
- 6 `ST` on two planes
- 3 `GW`
- plus a restored compile-time `B00 miniInternet`
- fixed intra-plane `ISL`
- slot-based runtime `UT-ST`, `GW-ST`, and cross-plane `ST-ST` links

The runtime control center is
[controller.py](/home/seed/seed-emulator/examples/satellite/controller.py:1).
SEED creates the base containers, the fixed intra-plane links, and the
compile-time ground-side `miniInternet` structure, including `IX100-IX105`,
transit ASes, stub ASes, and the `GW` uplinks toward Internet exchanges.

## Current Phase Boundary

This phase does only two things:

- restore the full compile-time `B00 miniInternet`
- keep the slot-based satellite runtime link model operational and inspectable

This phase explicitly does not do the following:

- it does not require `UT` to ping through to the `miniInternet`
- it does not add temporary `route/NAT`
- it does not restore the old `forwarding_state`, `candidate`, or `snapshot` workflows
- it does not introduce real `MPLS`
- the satellite runtime side does not run `OSPF/iBGP/eBGP`

So the current ground-side Internet and the runtime satellite link layer
coexist structurally, but end-to-end forwarding between them is intentionally
left for the next stage.

## Current Link Model

- `UT-ST`: each `UT` owns 2 access slots
- `GW-ST`: each `GW` owns 2 gateway slots
- cross-plane `ST-ST`: orbit-a and orbit-b share 3 reusable slots
- intra-plane `ST-ST`: fixed `front/back` links are compile-time created and always on

Each slot is one Docker bridge network. `UT` and `GW` fixed endpoints are
preconnected during `--init-slot-runtime`. `ST` endpoints are connected and
disconnected at runtime.
The `miniInternet` internal links and `GW` IX uplinks remain compile-time
structures and are not managed by the slot runtime.

## State Files

- `configs/topology.json`: static inventory with fixed links and slot inventory
- `configs/link_state.json`: runtime slot/link state owned by the controller

Older `attach_state.json`, `topology_state.json`, and `forwarding_state.json`
are no longer part of the active prototype workflow.

`link_state.json` remains limited to the satellite slot runtime plus `fixed_isl`
entries. It does not mirror the internal links of the ground-side
`miniInternet`.

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

## Build And Start

```bash
python3 examples/satellite/build_topology.py
cd examples/satellite/output
docker-compose up -d
python3 /home/seed/seed-emulator/examples/satellite/controller.py --init-slot-runtime
```

## Minimal Validation

View current slot state:

```bash
python3 examples/satellite/controller.py --show-link-state
```

Count Docker bridge networks created for the prototype:

```bash
docker network ls --format '{{.Name}}' | grep -E '(access-slot|gateway-slot|cross-slot|^link-a|^link-b)'
```

Count interfaces inside one container:

```bash
docker exec ut1 ip -o link show | wc -l
docker exec gw1 ip -o link show | wc -l
docker exec sat-a1 ip -o link show | wc -l
```

Trigger one `UT-ST` dynamic attach:

```bash
python3 examples/satellite/controller.py --attach-ut ut1:sat-a1
python3 examples/satellite/controller.py --attach-ut ut1:sat-a1 --slot-name ut1-access-slot1
```

Release that `UT` slot:

```bash
python3 examples/satellite/controller.py --detach-ut ut1
```

Trigger one `GW-ST` dynamic attach:

```bash
python3 examples/satellite/controller.py --attach-gw gw1:sat-b1
python3 examples/satellite/controller.py --attach-gw gw1:sat-b1 --slot-name gw1-gateway-slot1
```

Release that `GW` slot:

```bash
python3 examples/satellite/controller.py --detach-gw gw1
```

Trigger one cross-plane dynamic connection:

```bash
python3 examples/satellite/controller.py --connect-cross sat-a1:sat-b1:left:right
python3 examples/satellite/controller.py --connect-cross sat-a1:sat-b1:left:right --slot-name ab-cross-slot2
```

Release that cross-plane slot:

```bash
python3 examples/satellite/controller.py --release-slot ab-cross-slot0
```

Inspect one container after runtime attach:

```bash
docker exec sat-a1 ip -o addr show
docker exec ut1 ip -o addr show
docker exec gw1 ip -o addr show
```

## Runtime Demo

A serial handover demo script is available at:

`examples/satellite/demo_runtime_handover.py`

Example:

```bash
python3 examples/satellite/demo_runtime_handover.py --interval 5 --iterations 2
```

The demo alternates between:

- `t0`: `ut1 -> sat-a1`, `gw1 -> sat-b1`, cross-plane `sat-a1 <-> sat-b1`
- `t1`: release cross-plane, detach `ut1`, detach `gw1`, then `ut1 -> sat-a2`, `gw1 -> sat-b2`, cross-plane `sat-a2 <-> sat-b2`

After each serial runtime operation, the script prints:

- current active links
- current occupied slots
- the runtime operation being executed
- a `link_state.json` summary
- simple reachability probes for the current `UT`, `GW`, and cross-plane access IPs

The demo intentionally reuses the same slot names:

- `ut1-access-slot0`
- `gw1-gateway-slot0`
- `ab-cross-slot0`

So the probe IPs are slot-peer IPs, not satellite-specific IPs. During
handover, the `binding` and container interfaces change while the probe IPs
remain the same.

This current demo only validates slot-runtime attach/detach/connect/release,
slot reuse, state consistency, and visualization/inspection workflow. It does
not claim that `UT -> miniInternet` forwarding is already implemented, and it
does not add temporary `route/NAT` or restore the old forwarding baseline.

## Safety Rules Implemented In Controller

- after each `docker network connect`, the target container must gain exactly 1 new interface
- the controller only renames the discovered new interface
- if validation fails, the controller rolls back the partial runtime connect
- `link_state.json` is written only after runtime operations succeed
- cross-plane connect uses two-step connect with rollback if the second `ST` fails
- if `--slot-name` is not provided, the controller uses the first free slot in sorted slot-name order

## Bootstrap Interface Note

- some containers may still show `eth*` interfaces left from the SEED bootstrap stage in a `DOWN` state
- this is not a slot-runtime bug by itself
- slot validation is based on the interface diff immediately before and after `docker network connect`
- the controller cares about “exactly one newly appeared interface”, not about removing every old bootstrap-era `eth*`

## Routing Boundary

- the restored `miniInternet` keeps SEED-managed `Routing + Ebgp + Ibgp + Ospf`
- `GW` ground-side IX uplinks keep SEED-managed `eBGP` semantics
- `AS250` and the satellite-runtime side of `AS65000` are explicitly masked from SEED `Ospf/Ibgp`
- `UT-ST`, `GW-ST`, cross-plane `ST-ST`, and the current fixed intra-plane `ISL` remain controller-owned runtime topology semantics rather than SEED-managed satellite forwarding

## Observation Tips

- `SEED map` URL: `http://127.0.0.1:8080/pro/map`
- treat the current map as a compile-time topology view produced by the Docker compiler
- in the current version, that compile-time view should include the full restored `B00 miniInternet`, the `AS250` satellite side, and the `AS65000` gateways
- do not assume the map will reflect runtime `docker network connect/disconnect` slot changes in real time
- use the demo script output plus `python3 examples/satellite/controller.py --show-link-state` for runtime truth
- this phase does not provide a stable `UT -> ground host` ping target because forwarding/MPLS is intentionally out of scope
- this demo reuses the same `UT` access slot, so the recommended `UT`-side observation is to keep probing the same slot-peer IP while watching `binding.satellite_id` change:

```bash
while true; do
  date
  docker exec ut1 sh -lc 'ping -c 1 -W 1 10.90.11.1 >/dev/null 2>&1 && echo ut1-slot0 up || echo ut1-slot0 down'
  python3 examples/satellite/controller.py --show-link-state | grep 'ut1-access-slot0'
  sleep 1
done
```

- for `GW` feeder handover, keep probing the same slot-peer IP and watch `binding.satellite_id`:

```bash
while true; do
  date
  docker exec gw1 sh -lc 'ping -c 1 -W 1 10.91.11.1 >/dev/null 2>&1 && echo gw1-slot0 up || echo gw1-slot0 down'
  python3 examples/satellite/controller.py --show-link-state | grep 'gw1-gateway-slot0'
  sleep 1
done
```

- for cross-plane handover, watch the same cross-plane peer IP from `sat-a1` or `sat-a2` and pair it with the `ab-cross-slot0` binding:

```bash
while true; do
  date
  docker exec sat-a1 sh -lc 'ping -c 1 -W 1 10.92.1.2 >/dev/null 2>&1 && echo cross-slot0-from-a1 up || echo cross-slot0-from-a1 down'
  docker exec sat-a2 sh -lc 'ping -c 1 -W 1 10.92.1.2 >/dev/null 2>&1 && echo cross-slot0-from-a2 up || echo cross-slot0-from-a2 down'
  python3 examples/satellite/controller.py --show-link-state | grep 'ab-cross-slot0'
  sleep 1
done
```

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

The next forwarding/MPLS stage is expected to make the `UT stable identity
address/prefix` stay reachable across these runtime link-state changes while
using the slot-link addresses only as current next-hop information.

- the observation focus is not “IP changes from A to B”. It is:
  - ping may briefly fail during release/attach/connect
  - the same slot-peer IP becomes reachable again after handover
  - `link_state.json` binding moves from `sat-a1/sat-b1` to `sat-a2/sat-b2`

## Design Note

Detailed design notes are in
[docs/satellite_phase1_design.md](/home/seed/seed-emulator/examples/satellite/docs/satellite_phase1_design.md:1).
Although the file name is historical, the document now describes the current
Phase 2 slot-based prototype and supersedes the old candidate-link mainline.
