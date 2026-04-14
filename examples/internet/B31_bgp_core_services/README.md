# BGP Core Services Example

This example validates the promoted core backend services:

- `from seedemu.services import BGPControlService, BGPObservationService`
- router-first attachment to `AS2/r100`
- successful `render()` and Docker `compile()`
- endpoint metadata and exposure behavior after promotion into `seedemu/services/`

Current phase is backend-only:

- implemented: `--mode core`
- `--mode full` now includes the minimal Alice glue needed to expose birdwatcher on `29184` for host-side access
- this is a B31 full-mode glue fix only; core observation metadata still remains `exposure_mode=internal`

## Topology

- `AS2` is a transit AS with routers `r100` and `r101`
- `AS150` and `AS151` are stub ASes
- both core services attach to `AS2/r100`

## Run

```bash
cd /home/seed/seed-emulator/examples/internet/B31_bgp_core_services
python3 bgp_core_services.py --mode core --platform amd
```

This generates Docker output in `output/`.

## What This Verifies

### Control

- `BGPControlService` is imported from `seedemu.services`
- attached with router-first API to `AS2/r100`
- compiled with `host` exposure on port `18081`
- prints endpoint metadata after `emu.render()`

Expected script output includes:

```text
BGP control endpoints:
  AS2 r100: http://localhost:18081
    exposure_mode=host host_port=18081
```

Expected compile result:

- `output/docker-compose.yml` contains a port mapping for `brdnode_2_r100`
- the mapping includes `18081:18081/tcp`
- this endpoint is intentionally exposed for host-side access in the current example

### Observation

- `BGPObservationService` is imported from `seedemu.services`
- attached with router-first API to `AS2/r100`
- kept as internal observation backend
- prints endpoint metadata after `emu.render()`
- in `--mode full`, the example adds a minimal compose-level host port mapping so Alice can reach birdwatcher via `host.docker.internal:29184`

Expected script output includes:

```text
BGP observation endpoints:
  AS2 r100: http://10.0.0.1:29184
    exposure_mode=internal host_port=None
```

Expected compile result:

- in `--mode core`, `output/docker-compose.yml` does not add a host port mapping for birdwatcher
- in `--mode full`, the example glue adds `29184:29184` so Alice can reach birdwatcher through the host
- observation metadata still remains internal even when the full-mode glue exposes the port

## Why This Validates Core Promotion

This example exercises the promoted classes directly from `seedemu.services` without using the old `nsdi/bgp_services/` prototype path and without copying any B30 Alice glue.
