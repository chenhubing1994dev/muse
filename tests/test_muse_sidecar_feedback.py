from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.request import Request, urlopen

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import muse_sidecar_main as sidecar  # noqa: E402


class DesktopFeedbackStoreTests(unittest.TestCase):
    def test_feedback_api_reads_and_persists_feedback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = sidecar.MuseEngine(data_root=Path(tmp)).start()
            headers = {"Cookie": f"muse_session={engine.session_token}"}
            public_health_request = Request(engine.base_url + "__muse/health")
            with urlopen(public_health_request, timeout=5) as response:
                public_health = json.loads(response.read().decode("utf-8"))
                self.assertEqual(response.status, 200)
                self.assertTrue(public_health["agent_registry_configured"])
                self.assertTrue(public_health["stores_healthy"])
                self.assertEqual(
                    {key: value["status"] for key, value in public_health["stores"].items()},
                    {"feedback": "healthy", "inspiration": "healthy", "artifacts": "healthy", "agents": "healthy"},
                )
            try:
                agents_request = Request(engine.base_url + "api/v1/agents", headers=headers)
                with urlopen(agents_request, timeout=5) as response:
                    agents = json.loads(response.read().decode("utf-8"))
                    self.assertEqual(response.status, 200)
                    self.assertTrue(agents["registry_configured"])
                    self.assertEqual(agents["count"], 0)
                health_request = Request(engine.base_url + "api/v1/health", headers=headers)
                with urlopen(health_request, timeout=5) as response:
                    health = json.loads(response.read().decode("utf-8"))
                    self.assertTrue(health["agent_registry_configured"])
                    self.assertTrue(health["stores_healthy"])
                    self.assertEqual(
                        {key: value["status"] for key, value in health["stores"].items()},
                        {"feedback": "healthy", "inspiration": "healthy", "artifacts": "healthy", "agents": "healthy"},
                    )
                store_health_request = Request(engine.base_url + "api/v1/store-health", headers=headers)
                with urlopen(store_health_request, timeout=5) as response:
                    store_health = json.loads(response.read().decode("utf-8"))
                    self.assertEqual(response.status, 200)
                    self.assertTrue(store_health["stores_healthy"])

                request = Request(engine.base_url + "api/v1/feedback", headers=headers)
                with urlopen(request, timeout=5) as response:
                    listing = json.loads(response.read().decode("utf-8"))
                    self.assertEqual(response.status, 200)
                    self.assertEqual(listing["count"], 0)
                inspiration_request = Request(engine.base_url + "api/v1/inspirations", headers=headers)
                with urlopen(inspiration_request, timeout=5) as response:
                    inspirations = json.loads(response.read().decode("utf-8"))
                    self.assertEqual(response.status, 200)
                    self.assertEqual(inspirations["count"], 0)
                artifacts_request = Request(engine.base_url + "api/v1/artifacts", headers=headers)
                with urlopen(artifacts_request, timeout=5) as response:
                    artifacts = json.loads(response.read().decode("utf-8"))
                    self.assertEqual(response.status, 200)
                    self.assertEqual(artifacts["count"], 0)
                payload = json.dumps({"category": "usability", "tag": "hard_to_use", "severity": 2}).encode("utf-8")
                request = Request(engine.base_url + "api/v1/feedback", data=payload,
                                  headers={**headers, "Content-Type": "application/json"}, method="POST")
                with urlopen(request, timeout=5) as response:
                    saved = json.loads(response.read().decode("utf-8"))
                    self.assertEqual(response.status, 201)
                    self.assertEqual(saved["feedback"]["tag"], "hard_to_use")
            finally:
                engine.stop()


if __name__ == "__main__":
    unittest.main()
