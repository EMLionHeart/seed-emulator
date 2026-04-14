# RoST-Style Environment

This example is a RoST-style SEED environment prototype. It does not implement
the full RoST algorithm or cryptographic protocol. Its purpose is to show that
SEED can represent the environment structure and control mechanisms needed for
RoST-style experiments:

- a repository reachable over HTTP
- a host-side agent in adopting ASes
- a router-local helper that rewrites dynamic BIRD policy and runs `birdc configure`
- static suppressor-AS behavior injected at render time for selected prefixes

`bird.conf` is rendered by SEED and then patched in [rost_env.py](./rost_env.py).
Adopting ASes get dynamic policy through `/etc/bird/rost_policy.conf`.
Suppressor ASes do not run an agent or helper and use only static render-time
export withholding.

## What This Example Demonstrates

- A repository / agent / router-helper / BIRD split inside a SEED Internet example.
- Dynamic policy control for adopting ASes without rewriting `bird.conf` at runtime.
- A static suppressor routing role that withholds selected prefixes on export.
- Prefix-specific experiments using simple BIRD policy changes instead of protocol changes.

This example demonstrates the environment and control path. It does not claim
to implement full RoST validation, protocol signaling, or protocol-level
withdraw handling.

## Topology And Roles

The example builds a small multi-AS Internet with transit-like and stub ASes.
With the current defaults:

- Repository AS: `154`
- Repository validation target: `10.154.0.0/24`
- Dynamic policy demo target: `10.153.0.0/24`
- Suppressor ASes: `3`
- Suppressor target prefixes: `10.155.0.0/24`
- Current patched suppressor router: AS3 anchor router `r100`

Adopting ASes are selected deterministically from the configured seed and
adoption rate. With the current defaults, the adopting ASes are:

- `2`
- `4`
- `150`
- `152`
- `153`

## Build And Run

From this example directory:

```bash
cd nsdi/rost
python3 rost_env.py amd
cd output
docker-compose up -d
```

Use `python3 rost_env.py arm` instead on ARM64 hosts.

To stop the environment:

```bash
cd nsdi/rost/output
docker-compose down
```

## Visualization And Exploration

After the environment is running, you can use the SEED map UI at
`http://localhost:8080/pro/map`.

The map is useful for:

- inspecting nodes, links, and IP addresses
- understanding how the experiment topology is connected
- conveniently opening terminals in containers while exploring the artifact

This is often the fastest way to orient yourself before running the detailed
validation workflow.

## Validation

[VALIDATION.md](./VALIDATION.md) is the primary reviewer-facing validation
document. It contains the full step-by-step behavioral workflow and keeps the
evidence hierarchy explicit:

- primary evidence: repository reachability, BIRD route visibility, export behavior, and downstream effects
- secondary evidence: helper state, generated policy files, and other implementation/debugging checks

Use this README as the entry point for setup and orientation. Use
[VALIDATION.md](./VALIDATION.md) for the detailed validation procedure.

## Key Files

- [rost_env.py](./rost_env.py): builds the topology, assigns roles, deploys the repository, agent, and helper, and patches router BIRD config
- [agent.py](./agent.py): capability-check client and helper control client
- [router_helper.py](./router_helper.py): helper HTTP server, policy-state manager, and `birdc` integration
- [repo_server.py](./repo_server.py): minimal repository HTTP service

## Notes And Limitations

- This is a RoST-style environment prototype, not a full RoST implementation.
- The repository is intentionally minimal.
- Suppressor behavior is static and configured at render time only.
- Suppressor ASes do not have runtime control via helper or agent.
- The example does not modify the SEED BGP implementation.
- The example does not implement protocol-level withdraw handling.
- Adopting-router helper state is reset to an empty baseline on startup unless `router_helper.py` is launched manually with `--preserve-state`.
- The suppressor patch is intentionally narrow and currently targets only the suppressor anchor router selected by the existing helper function.
