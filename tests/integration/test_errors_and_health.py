from fastapi.testclient import TestClient

from app.main import create_app


def test_health_and_readiness(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}
    body = client.get("/health/ready").json()
    assert body["status"] in {"ok", "degraded"}
    assert body["checks"]["postgres"] == "ok"


def test_swagger_and_openapi_are_served(client: TestClient) -> None:
    assert client.get("/docs").status_code == 200
    assert client.get("/redoc").status_code == 200
    spec = client.get("/openapi.json").json()
    assert "/api/v1/payments/webhook/" in spec["paths"]
    assert "OAuth2PasswordBearer" in spec["components"]["securitySchemes"]
    # error responses are documented with the shared envelope
    assert "ErrorResponse" in spec["components"]["schemas"]


def test_unknown_route_and_wrong_method_use_the_error_envelope(client: TestClient) -> None:
    missing = client.get("/api/v1/nope")
    assert (missing.status_code, missing.json()["error"]["code"]) == (404, "NOT_FOUND")
    wrong = client.delete("/api/v1/centres")
    assert (wrong.status_code, wrong.json()["error"]["code"]) == (405, "METHOD_NOT_ALLOWED")


def test_request_id_is_generated_echoed_and_present_in_errors(client: TestClient) -> None:
    generated = client.get("/health")
    assert len(generated.headers["X-Request-ID"]) >= 16
    echoed = client.get("/api/v1/nope", headers={"X-Request-ID": "trace-me-123"})
    assert echoed.headers["X-Request-ID"] == "trace-me-123"
    assert echoed.json()["error"]["request_id"] == "trace-me-123"


def test_unexpected_exception_returns_generic_500_without_leaking_details(
    client: TestClient,
) -> None:
    app = create_app()

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("secret internal detail: db password is hunter2")

    with TestClient(app, raise_server_exceptions=False) as safe_client:
        response = safe_client.get("/boom")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "hunter2" not in response.text
    assert "Traceback" not in response.text
