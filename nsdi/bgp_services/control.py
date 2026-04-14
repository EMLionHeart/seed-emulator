"""BGP control service."""

from __future__ import annotations

import re
from dataclasses import dataclass
from textwrap import dedent
from typing import Any, Dict, Iterable, List, Optional, Tuple

from seedemu.core import Emulator, Layer, Router

from .helpers import (
    BIRD_CONFIG_PATH,
    BIRD_CONTROL_SOCKET_PATH,
    CONTROL_INCLUDE_PATH,
    CONTROL_LOG_PATH,
    CONTROL_SCRIPT_PATH,
    CONTROL_STATE_PATH,
    EXPOSURE_LOCAL,
    ExposureConfig,
    apply_exposure,
    build_endpoint_metadata,
    ensure_bird_include,
    ensure_directory_start_command,
    normalize_exposure,
    patch_bird_config,
    path_str,
    resolve_router,
    set_node_json_file,
    set_node_text_file,
    shell_quote,
)


DEFAULT_CONTROL_PORT = 18081


@dataclass(frozen=True)
class _ControlTarget:
    router: Router
    port: int
    preserve_state: bool
    exposure: ExposureConfig


def _build_control_layout_metadata() -> Dict[str, Any]:
    return {
        "model": "three-layer",
        "static_bird_config_path": path_str(BIRD_CONFIG_PATH),
        "runtime_policy_include_path": path_str(CONTROL_INCLUDE_PATH),
        "runtime_state_path": path_str(CONTROL_STATE_PATH),
        "runtime_control_script_path": path_str(CONTROL_SCRIPT_PATH),
        "bird_control_socket_path": path_str(BIRD_CONTROL_SOCKET_PATH),
    }


def _sanitize_bird_identifier(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", value)


def _build_filter_definition(filter_name: str, body_lines: Iterable[str]) -> str:
    indented = "\n".join(f"    {line}" for line in body_lines)
    return f"filter {filter_name} {{\n{indented}\n}}\n"


def _normalize_original_import_lines(import_body: str) -> List[str]:
    lines = [line.strip() for line in import_body.strip().splitlines()]
    return lines if lines else ["accept;"]


def _normalize_original_export_expression(export_expr: str) -> str:
    expr = export_expr.strip()
    if expr == "all":
        return "true"
    if expr == "none":
        return "false"
    if expr.startswith("where "):
        return expr[len("where ") :].strip()
    return expr


def _build_import_wrapper_lines(original_import_lines: Iterable[str]) -> List[str]:
    lines = [
        "# Control pre-checks run before preserved SEED import semantics.",
        "if bgp_control_is_enabled() && bgp_control_import_is_rejected() then reject;",
        "# Preserved SEED import filter semantics.",
    ]
    lines.extend(original_import_lines)
    return lines


def _build_export_wrapper_lines(original_export_expr: str) -> List[str]:
    return [
        "# Preserve the original SEED export condition first.",
        f"if !({original_export_expr}) then reject;",
        "# Control logic only narrows or annotates routes after the original check.",
        "if !bgp_control_is_enabled() then accept;",
        "if bgp_control_export_is_denied() then reject;",
        "if !bgp_control_export_is_allowed() then reject;",
        "bgp_control_apply_export_attributes();",
        "accept;",
    ]


def _build_wrapped_ebgp_protocol(
    protocol_name: str,
    local_asn: Tuple[str, str],
    peer_asn: Tuple[str, str],
    import_body: str,
    export_expr: str,
) -> str:
    tag = _sanitize_bird_identifier(protocol_name)
    import_filter_name = f"bgp_control_import_wrapper_{tag}"
    export_filter_name = f"bgp_control_export_wrapper_{tag}"
    original_import_lines = _normalize_original_import_lines(import_body)
    original_export_expr = _normalize_original_export_expression(export_expr)

    filter_defs = (
        "\n"
        + f"# BGPControlService wrapper for protocol {protocol_name}.\n"
        + "# The original SEED filter intent remains in bird.conf and is wrapped by control guards.\n"
        + _build_filter_definition(
            import_filter_name,
            _build_import_wrapper_lines(original_import_lines),
        )
        + _build_filter_definition(
            export_filter_name,
            _build_export_wrapper_lines(original_export_expr),
        )
    )

    protocol_body = f"""protocol bgp {protocol_name} {{
    ipv4 {{
        table t_bgp;
        import filter {import_filter_name};
        export filter {export_filter_name};
        next hop self;
    }};
    local {local_asn[0]} as {local_asn[1]};
    neighbor {peer_asn[0]} as {peer_asn[1]};
}}"""
    return filter_defs + protocol_body


def _patch_control_bird_conf(bird_conf: str) -> str:
    """Inject control wrappers while preserving the original SEED filter intent.

    Render-time patching is limited to:
    - adding the dynamic control include
    - turning inline protocol filter semantics into wrapper filters that retain
      the original import/export decisions and add control checks around them

    Runtime control never rewrites these protocol blocks again.
    """

    protocol_pattern = re.compile(
        r"""protocol\s+bgp\s+(?P<name>\S+)\s*\{
\s*ipv4\s*\{
\s*table\s+t_bgp;
\s*import\s+filter\s*\{
(?P<import_body>.*?)
\s*\};
\s*export\s+(?P<export_expr>.*?);
\s*next\s+hop\s+self;
\s*\};
\s*local\s+(?P<local_addr>\S+)\s+as\s+(?P<local_asn>\d+);
\s*neighbor\s+(?P<peer_addr>\S+)\s+as\s+(?P<peer_asn>\d+);
\s*\}""",
        flags=re.DOTALL,
    )

    def replace_protocol(match: re.Match[str]) -> str:
        local_asn = match.group("local_asn")
        peer_asn = match.group("peer_asn")
        if local_asn == peer_asn:
            return match.group(0)

        return _build_wrapped_ebgp_protocol(
            protocol_name=match.group("name"),
            local_asn=(match.group("local_addr"), local_asn),
            peer_asn=(match.group("peer_addr"), peer_asn),
            import_body=match.group("import_body"),
            export_expr=match.group("export_expr"),
        )

    return protocol_pattern.sub(replace_protocol, bird_conf)


def _build_initial_control_policy() -> str:
    return dedent(
        """\
        # Managed by BGPControlService.
        # This file contains dynamic control-plane state only.

        function bgp_control_is_enabled()
        {
            return false;
        }

        function bgp_control_export_is_allowed()
        {
            return false;
        }

        function bgp_control_export_is_denied()
        {
            return false;
        }

        function bgp_control_apply_export_attributes()
        {
        }

        function bgp_control_import_is_rejected()
        {
            return false;
        }
        """
    )


def _build_initial_control_state() -> Dict[str, Any]:
    return {
        "enabled": False,
        "export_allow_prefixes": [],
        "export_deny_prefixes": [],
        "export_attribute_patches": [],
        "import_reject_prefixes": [],
    }


def _build_control_script(default_host: str, default_port: int) -> str:
    script = dedent(
        """\
        #!/usr/bin/env python3
        \"\"\"Router-local BGP control service.\"\"\"

        import argparse
        import ipaddress
        import json
        import subprocess
        import threading
        from http import HTTPStatus
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from pathlib import Path


        DEFAULT_HOST = "__DEFAULT_HOST__"
        DEFAULT_PORT = __DEFAULT_PORT__
        POLICY_PATH = Path("__POLICY_PATH__")
        STATE_PATH = Path("__STATE_PATH__")


        def run_command(argv):
            try:
                result = subprocess.run(
                    argv,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=10,
                )
            except FileNotFoundError as exc:
                return False, f"command not found: {exc.filename}"
            except subprocess.TimeoutExpired:
                return False, "command timed out"
            except Exception as exc:
                return False, str(exc)

            if result.returncode != 0:
                stderr = result.stderr.strip() or "no stderr"
                return False, f"exit {result.returncode}: {stderr}"

            return True, result.stdout.strip() or "(no output)"


        def canonicalize_prefix(prefix):
            network = ipaddress.ip_network(prefix, strict=False)
            if network.version != 4:
                raise ValueError("only IPv4 prefixes are supported")
            return str(network)


        def canonicalize_prefixes(prefixes):
            return sorted({canonicalize_prefix(prefix) for prefix in prefixes})


        def normalize_state(raw_state):
            state = {
                "enabled": bool(raw_state.get("enabled", False)),
                "export_allow_prefixes": [],
                "export_deny_prefixes": [],
                "export_attribute_patches": list(raw_state.get("export_attribute_patches", [])),
                "import_reject_prefixes": [],
            }

            for key in (
                "export_allow_prefixes",
                "export_deny_prefixes",
                "import_reject_prefixes",
            ):
                prefixes = raw_state.get(key, [])
                if not isinstance(prefixes, list):
                    raise ValueError(f"{key} must be a list")
                state[key] = canonicalize_prefixes(prefixes)

            return state


        def render_prefix_match(prefixes, indent):
            joined = ", ".join(prefixes)
            return f'{" " * indent}if net ~ [ {joined} ] then return true;'


        def render_policy_conf(state):
            enabled_value = "true" if state["enabled"] else "false"
            lines = [
                "# Managed by BGPControlService.",
                f"# Regenerated from {STATE_PATH}.",
                "",
                "function bgp_control_is_enabled()",
                "{",
                f"    return {enabled_value};",
                "}",
                "",
                "function bgp_control_export_is_allowed()",
                "{",
            ]

            if state["export_allow_prefixes"]:
                lines.append(render_prefix_match(state["export_allow_prefixes"], 4))
            lines.extend(
                [
                    "    return false;",
                    "}",
                    "",
                    "function bgp_control_export_is_denied()",
                    "{",
                ]
            )

            if state["export_deny_prefixes"]:
                lines.append(render_prefix_match(state["export_deny_prefixes"], 4))
            lines.extend(
                [
                    "    return false;",
                    "}",
                    "",
                    "function bgp_control_apply_export_attributes()",
                    "{",
                    "}",
                    "",
                    "function bgp_control_import_is_rejected()",
                    "{",
                ]
            )

            if state["import_reject_prefixes"]:
                lines.append(render_prefix_match(state["import_reject_prefixes"], 4))
            lines.extend(
                [
                    "    return false;",
                    "}",
                    "",
                ]
            )
            return "\\n".join(lines)


        def list_bgp_protocols():
            ok, detail = run_command(["birdc", "show", "protocols"])
            if not ok:
                return False, detail

            protocols = []
            for raw_line in detail.splitlines():
                line = raw_line.strip()
                if not line:
                    continue

                parts = line.split()
                if len(parts) >= 2 and parts[1] == "BGP":
                    protocols.append(parts[0])

            return True, protocols


        def refresh_bgp_protocols():
            ok, protocols = list_bgp_protocols()
            if not ok:
                return False, {
                    "protocols": [],
                    "detail": protocols,
                    "results": [],
                }

            results = []
            for protocol in protocols:
                disable_ok, disable_detail = run_command(["birdc", "disable", protocol])
                enable_ok, enable_detail = run_command(["birdc", "enable", protocol])
                results.append(
                    {
                        "protocol": protocol,
                        "disable": {"ok": disable_ok, "detail": disable_detail},
                        "enable": {"ok": enable_ok, "detail": enable_detail},
                    }
                )

            refresh_ok = all(
                entry["disable"]["ok"] and entry["enable"]["ok"]
                for entry in results
            )
            return refresh_ok, {
                "protocols": protocols,
                "detail": f"refreshed {len(protocols)} BGP protocol(s)",
                "results": results,
            }


        class PolicyManager:
            def __init__(self, state_path=STATE_PATH, policy_path=POLICY_PATH, preserve_state=False):
                self._state_path = Path(state_path)
                self._policy_path = Path(policy_path)
                self._preserve_state = preserve_state
                self._lock = threading.Lock()
                self._state = self._load_state()
                self._write_state()
                self._write_policy_conf()

            def snapshot(self):
                with self._lock:
                    return json.loads(json.dumps(self._state))

            def apply(self, mutator=None, refresh_bgp=False):
                with self._lock:
                    if mutator is not None:
                        mutator(self._state)
                        self._state = normalize_state(self._state)

                    self._write_state()
                    self._write_policy_conf()
                    ok, detail = run_command(["birdc", "configure"])
                    refresh_ok = True
                    refresh_payload = None
                    if ok and refresh_bgp:
                        refresh_ok, refresh_payload = refresh_bgp_protocols()

                    payload = {
                        "status": "ok" if ok and refresh_ok else "error",
                        "bird_configure": {"ok": ok, "detail": detail},
                        "state": json.loads(json.dumps(self._state)),
                    }
                    if refresh_payload is not None:
                        payload["bgp_refresh"] = {"ok": refresh_ok, **refresh_payload}

                    status = HTTPStatus.OK if ok and refresh_ok else HTTPStatus.INTERNAL_SERVER_ERROR
                    return status, payload

            def _load_state(self):
                if not self._preserve_state or not self._state_path.exists():
                    return normalize_state({})

                with self._state_path.open("r", encoding="utf-8") as handle:
                    return normalize_state(json.load(handle))

            def _write_state(self):
                self._state_path.parent.mkdir(parents=True, exist_ok=True)
                with self._state_path.open("w", encoding="utf-8") as handle:
                    json.dump(self._state, handle, indent=2, sort_keys=True)
                    handle.write("\\n")

            def _write_policy_conf(self):
                self._policy_path.parent.mkdir(parents=True, exist_ok=True)
                self._policy_path.write_text(render_policy_conf(self._state), encoding="utf-8")


        class ControlServer(ThreadingHTTPServer):
            def __init__(self, server_address, handler_cls, policy_manager):
                super().__init__(server_address, handler_cls)
                self.policy_manager = policy_manager


        class ControlHandler(BaseHTTPRequestHandler):
            server_version = "BGPControlService/0.1"

            def do_GET(self):
                if self.path == "/healthz":
                    self._send_json({"status": "ok"})
                    return

                if self.path == "/state":
                    self._send_json({"status": "ok", "state": self.server.policy_manager.snapshot()})
                    return

                self.send_error(HTTPStatus.NOT_FOUND, "not found")

            def do_POST(self):
                routes = {
                    "/policy/enable": self._post_enable,
                    "/policy/disable": self._post_disable,
                    "/policy/export/allow": self._post_export_allow,
                    "/policy/export/deny": self._post_export_deny,
                    "/policy/import/reject": self._post_import_reject,
                    "/policy/reload": self._post_reload,
                }

                handler = routes.get(self.path)
                if handler is None:
                    self.send_error(HTTPStatus.NOT_FOUND, "not found")
                    return

                try:
                    payload = self._read_json_payload()
                    status, response = handler(payload)
                except ValueError as exc:
                    self._send_json({"status": "error", "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                    return

                self._send_json(response, status=status)

            def log_message(self, fmt, *args):
                return

            def _post_enable(self, _payload):
                return self.server.policy_manager.apply(
                    lambda state: state.__setitem__("enabled", True)
                )

            def _post_disable(self, _payload):
                return self.server.policy_manager.apply(
                    lambda state: state.__setitem__("enabled", False)
                )

            def _post_export_allow(self, payload):
                prefixes = self._require_prefixes(payload)
                return self.server.policy_manager.apply(
                    lambda state: self._add_prefixes(state["export_allow_prefixes"], prefixes),
                    refresh_bgp=True,
                )

            def _post_export_deny(self, payload):
                prefixes = self._require_prefixes(payload)
                return self.server.policy_manager.apply(
                    lambda state: self._add_prefixes(state["export_deny_prefixes"], prefixes),
                    refresh_bgp=True,
                )

            def _post_import_reject(self, payload):
                prefixes = self._require_prefixes(payload)
                return self.server.policy_manager.apply(
                    lambda state: self._add_prefixes(state["import_reject_prefixes"], prefixes),
                    refresh_bgp=True,
                )

            def _post_reload(self, payload):
                return self.server.policy_manager.apply(
                    refresh_bgp=bool(payload.get("refresh_bgp", True))
                )

            def _send_json(self, payload, status=HTTPStatus.OK):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _read_json_payload(self):
                content_length = int(self.headers.get("Content-Length", "0"))
                if content_length == 0:
                    return {}

                body = self.rfile.read(content_length).decode("utf-8")
                if not body.strip():
                    return {}

                payload = json.loads(body)
                if not isinstance(payload, dict):
                    raise ValueError("JSON payload must be an object")
                return payload

            def _require_prefixes(self, payload):
                if "prefix" in payload:
                    return [canonicalize_prefix(payload["prefix"])]

                prefixes = payload.get("prefixes")
                if not isinstance(prefixes, list) or len(prefixes) == 0:
                    raise ValueError("payload must include 'prefix' or non-empty 'prefixes'")
                return canonicalize_prefixes(prefixes)

            def _add_prefixes(self, target, prefixes):
                target[:] = sorted(set(target).union(prefixes))


        def main():
            parser = argparse.ArgumentParser(description="Router-local BGP control service")
            parser.add_argument("--host", default=DEFAULT_HOST)
            parser.add_argument("--port", type=int, default=DEFAULT_PORT)
            parser.add_argument("--preserve-state", action="store_true")
            args = parser.parse_args()

            policy_manager = PolicyManager(preserve_state=args.preserve_state)
            server = ControlServer((args.host, args.port), ControlHandler, policy_manager)
            server.serve_forever()


        if __name__ == "__main__":
            main()
        """
    )
    return (
        script.replace("__DEFAULT_PORT__", str(default_port))
        .replace("__DEFAULT_HOST__", default_host)
        .replace("__POLICY_PATH__", path_str(CONTROL_INCLUDE_PATH))
        .replace("__STATE_PATH__", path_str(CONTROL_STATE_PATH))
    )


class BGPControlService(Layer):
    """Install a router-local HTTP control service for BGP policy toggles."""

    def __init__(self):
        super().__init__()
        self._targets: Dict[Tuple[int, str], _ControlTarget] = {}
        self._endpoints: List[Dict[str, Any]] = []
        self.addDependency("Routing", False, False)
        self.addDependency("Ebgp", False, True)
        self.addDependency("Ibgp", False, True)

    def getName(self) -> str:
        return "BGPControlService"

    def attachRouter(
        self,
        target: Any,
        router_name: Optional[str] = None,
        *,
        port: int = DEFAULT_CONTROL_PORT,
        preserve_state: bool = False,
        exposure_mode: str = EXPOSURE_LOCAL,
        host_port: Optional[int] = None,
    ) -> "BGPControlService":
        # Router-first is the only primary attachment model.
        # `as_obj` + `router_name` remains as a compatibility entry point only.
        attachment = resolve_router(target, router_name)
        self._targets[attachment.key()] = _ControlTarget(
            router=attachment.router,
            port=port,
            preserve_state=preserve_state,
            exposure=normalize_exposure(exposure_mode, host_port=host_port),
        )
        return self

    def attachRouters(self, attachments: Iterable[Dict[str, Any]]) -> "BGPControlService":
        # Batch attachment follows the same rule: router-first is primary, and
        # `as_obj` is accepted only as a compatibility input shape.
        for attachment in attachments:
            self.attachRouter(
                attachment.get("router", attachment.get("as_obj")),
                attachment.get("router_name"),
                port=attachment.get("port", DEFAULT_CONTROL_PORT),
                preserve_state=attachment.get("preserve_state", False),
                exposure_mode=attachment.get("exposure_mode", EXPOSURE_LOCAL),
                host_port=attachment.get("host_port"),
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
        out += f"BGPControlServiceLayer targets={len(self._targets)}\n"
        return out

    def _install_target(self, target: _ControlTarget) -> None:
        router = target.router
        exposure = apply_exposure(router, target.exposure, target.port)
        router.addSoftware("python3")

        ensure_directory_start_command(router, CONTROL_INCLUDE_PATH.parent, CONTROL_LOG_PATH.parent)
        ensure_bird_include(
            router,
            CONTROL_INCLUDE_PATH,
            marker="Managed by BGPControlService",
        )
        patch_bird_config(router, _patch_control_bird_conf)
        set_node_text_file(router, CONTROL_INCLUDE_PATH, _build_initial_control_policy())
        set_node_json_file(router, CONTROL_STATE_PATH, _build_initial_control_state())
        set_node_text_file(
            router,
            CONTROL_SCRIPT_PATH,
            _build_control_script(exposure.bind_host(), target.port),
        )

        router.appendStartCommand(f"chmod +x {shell_quote(CONTROL_SCRIPT_PATH)}")
        router.appendStartCommand(
            f"while [ ! -e {shell_quote(BIRD_CONTROL_SOCKET_PATH)} ]; do echo \"bgp-control: waiting for bird\"; sleep 1; done"
        )

        command = (
            # Runtime only touches CONTROL_INCLUDE_PATH / CONTROL_STATE_PATH and triggers birdc configure.
            f"python3 {shell_quote(CONTROL_SCRIPT_PATH)} --port {target.port}"
            f" --host {shell_quote(exposure.bind_host())}"
            f"{' --preserve-state' if target.preserve_state else ''}"
            f" >{shell_quote(CONTROL_LOG_PATH)} 2>&1"
        )
        router.appendStartCommand(command, True)

        endpoint = build_endpoint_metadata(router, target.port, exposure)
        router.setAttribute("bgp_control_port", target.port)
        router.setAttribute("bgp_control_endpoint", endpoint["endpoint"])
        router.setAttribute("bgp_control_endpoint_metadata", endpoint)
        router.setAttribute("bgp_control_layout", _build_control_layout_metadata())
        self._endpoints.append(endpoint)
