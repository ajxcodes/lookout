from pathlib import Path

from auth_test_helpers import make_client

from lookout.config.schema import LookoutConfig


def test_reset_execute_requires_confirmation_token(tmp_path: Path) -> None:
    client, app, path = make_client(tmp_path, LookoutConfig())
    res = client.post("/api/system/reset/execute", json={"scope": "camera_config"})
    assert res.status_code >= 400

    # with confirm works
    res2 = client.post("/api/system/reset/execute", json={"confirm": "RESET", "scope": "camera_config"})
    assert res2.status_code < 300
