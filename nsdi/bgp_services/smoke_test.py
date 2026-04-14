"""Lightweight smoke tests for nsdi.bgp_services."""

from __future__ import annotations

import unittest

from seedemu.core import AutonomousSystem

from nsdi.bgp_services.control import BGPControlService
from nsdi.bgp_services.helpers import (
    CONTROL_INCLUDE_PATH,
    CONTROL_SCRIPT_PATH,
    CONTROL_STATE_PATH,
)


MINIMAL_BIRD_CONF = """\
router id 10.0.0.1;
ipv4 table t_bgp;
protocol bgp test_peer {
    ipv4 {
        table t_bgp;
        import filter {
            if net = 10.150.0.0/24 then accept;
            reject;
        };
        export where net ~ [ 10.2.0.0/24+ ];
        next hop self;
    };
    local 10.0.0.1 as 2;
    neighbor 10.0.0.2 as 150;
}
"""


def _make_router():
    asys = AutonomousSystem(64512)
    router = asys.createRouter("r1")
    router.doRegister(str(asys.getAsn()), "rnode", router.getName())
    router.setLoopbackAddress("10.0.0.1")
    router.setFile("/etc/bird/bird.conf", MINIMAL_BIRD_CONF)
    return router


class BGPControlServiceSmokeTest(unittest.TestCase):
    def test_default_exposure_is_local(self):
        router = _make_router()

        service = BGPControlService()
        service.attachRouter(router)
        service.render(None)

        endpoint = service.getEndpoints()[0]
        start_commands = [cmd for (cmd, _fork) in router.getStartCommands()]

        self.assertEqual(endpoint["exposure_mode"], "local")
        self.assertEqual(endpoint["bind_host"], "127.0.0.1")
        self.assertEqual(endpoint["endpoint"], "http://127.0.0.1:18081")
        self.assertIsNone(endpoint["host_port"])
        self.assertIsNone(endpoint["endpoints"]["internal"])
        self.assertEqual(router.getPorts(), [])
        self.assertTrue(
            any("--host 127.0.0.1" in command for command in start_commands),
            "control start command must bind locally by default",
        )

    def test_host_exposure_adds_port_forwarding_and_metadata(self):
        router = _make_router()

        service = BGPControlService()
        service.attachRouter(router, exposure_mode="host", host_port=28081)
        service.render(None)

        endpoint = service.getEndpoints()[0]
        start_commands = [cmd for (cmd, _fork) in router.getStartCommands()]

        self.assertEqual(endpoint["exposure_mode"], "host")
        self.assertEqual(endpoint["bind_host"], "0.0.0.0")
        self.assertEqual(endpoint["host_port"], 28081)
        self.assertEqual(endpoint["endpoint"], "http://localhost:28081")
        self.assertEqual(endpoint["endpoints"]["internal"], "http://10.0.0.1:18081")
        self.assertIn((28081, 18081, "tcp"), router.getPorts())
        self.assertTrue(
            any("--host 0.0.0.0" in command for command in start_commands),
            "host exposure must bind on all interfaces inside the container",
        )

    def test_wrapper_patch_preserves_original_filter_intent(self):
        router = _make_router()

        service = BGPControlService()
        service.attachRouter(router)
        service.render(None)

        bird_conf = router.getFile("/etc/bird/bird.conf").get()[1]
        layout = router.getAttribute("bgp_control_layout")

        self.assertIn(f'include "{CONTROL_INCLUDE_PATH}";', bird_conf)
        self.assertIn("bgp_control_import_wrapper_test_peer", bird_conf)
        self.assertIn("bgp_control_export_wrapper_test_peer", bird_conf)
        self.assertIn("if net = 10.150.0.0/24 then accept;", bird_conf)
        self.assertIn("if !(net ~ [ 10.2.0.0/24+ ]) then reject;", bird_conf)
        self.assertIn(
            "The original SEED filter intent remains in bird.conf and is wrapped by control guards.",
            bird_conf,
        )
        self.assertEqual(layout["runtime_policy_include_path"], str(CONTROL_INCLUDE_PATH))
        self.assertEqual(layout["runtime_state_path"], str(CONTROL_STATE_PATH))
        self.assertEqual(layout["runtime_control_script_path"], str(CONTROL_SCRIPT_PATH))


if __name__ == "__main__":
    unittest.main()
