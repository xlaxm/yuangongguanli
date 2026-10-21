from fastapi.testclient import TestClient

from main import app


def test_health_returns_ok() -> None:
    client = TestClient(app)

    try:
        response = client.get("/health")
    finally:
        client.close()

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
