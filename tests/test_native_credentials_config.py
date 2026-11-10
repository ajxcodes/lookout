from fastapi.testclient import TestClient

from lookout.adapters.memory import InMemoryEventRepository
from lookout.adapters.web.app import create_app
from lookout.config.schema import LookoutConfig


def test_native_credential_create_and_test() -> None:
    config = LookoutConfig()
    app = create_app(config=config, repository=InMemoryEventRepository())
    client = TestClient(app)
    response = client.post("/api/native/credentials", json={})
    assert response.status_code == 200
