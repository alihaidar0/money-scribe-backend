import pytest
from django.http import HttpRequest, HttpResponse
from django.test import RequestFactory

from config.health import HealthCheckMiddleware


def never_called(request: HttpRequest) -> HttpResponse:
    raise AssertionError("the health check must answer before the rest of the stack")


def forwarded(request: HttpRequest) -> HttpResponse:
    return HttpResponse("app", status=200)


def test_healthz_answers_ok_without_reaching_the_app(rf: RequestFactory) -> None:
    response = HealthCheckMiddleware(never_called)(rf.get("/healthz"))

    assert response.status_code == 200
    assert response.content == b"ok"
    assert response["Cache-Control"] == "no-store"


def test_healthz_reports_the_release_the_image_was_built_from(
    rf: RequestFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_RELEASE", "0123456789abcdef0123456789abcdef01234567")

    response = HealthCheckMiddleware(never_called)(rf.get("/healthz"))

    assert response["X-Release"] == "0123456789abcdef0123456789abcdef01234567"


def test_healthz_has_no_release_header_when_none_is_set(
    rf: RequestFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("APP_RELEASE", raising=False)

    response = HealthCheckMiddleware(never_called)(rf.get("/healthz"))

    assert "X-Release" not in response


def test_every_other_path_goes_to_the_app(rf: RequestFactory) -> None:
    response = HealthCheckMiddleware(forwarded)(rf.get("/anything"))

    assert response.content == b"app"
    assert "X-Release" not in response
