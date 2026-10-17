import importlib
import json
from pathlib import Path
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from config.api_docs import docs_enabled, docs_urlpatterns
from users.models import User
from users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

DOCS = "/api/docs/"
SCHEMA = "/api/schema/"
COMMITTED_SCHEMA = Path(__file__).resolve().parents[2] / "openapi" / "schema.yaml"


@pytest.fixture
def staff_client() -> Client:
    client = Client()
    client.force_login(UserFactory.create(is_staff=True))
    return client


@pytest.fixture
def user_client() -> Client:
    client = Client()
    client.force_login(UserFactory.create())
    return client


# --- who may open them ------------------------------------------------------------------------


@pytest.mark.parametrize("path", [DOCS, SCHEMA])
def test_an_anonymous_visitor_gets_a_404_not_the_admin_address(
    client: Client, path: str
) -> None:
    """A redirect to the admin sign-in would reveal the secret ADMIN_URL to anyone."""
    response = client.get(path)

    assert response.status_code == 404
    assert "Location" not in response


@pytest.mark.parametrize("path", [DOCS, SCHEMA])
def test_a_signed_in_user_who_is_not_staff_gets_a_404_too(
    user_client: Client, path: str
) -> None:
    response = user_client.get(path)

    assert response.status_code == 404
    assert "Location" not in response


@pytest.mark.parametrize("path", [DOCS, SCHEMA])
def test_an_inactive_staff_account_gets_a_404(path: str) -> None:
    client = Client()
    user = UserFactory.create(is_staff=True)
    client.force_login(user)
    user.is_active = False
    user.save(update_fields=["is_active"])

    assert client.get(path).status_code == 404


def test_a_bearer_token_does_not_open_the_docs(api_client: Any, user: User) -> None:
    """The docs are not an API operation: an access token is not a staff session."""
    response = api_client.get(DOCS, headers={"Authorization": "Bearer anything"})

    assert response.status_code == 404


# --- what staff see ---------------------------------------------------------------------------


def test_staff_get_swagger_ui_with_bundled_assets(staff_client: Client) -> None:
    response = staff_client.get(DOCS)

    assert response.status_code == 200
    page = response.content.decode()
    assert "swagger-ui" in page
    assert (
        "/static/drf_spectacular_sidecar/swagger-ui-dist/swagger-ui-bundle.js" in page
    )
    assert "http://" not in page.replace(
        "http://www.w3.org", ""
    )  # no CDN or outside address
    assert "cdn." not in page


def test_swagger_ui_is_pointed_at_the_live_schema(staff_client: Client) -> None:
    page = staff_client.get(DOCS).content.decode()

    assert reverse("api-schema") in page
    assert '"persistAuthorization": true' in page


@pytest.mark.parametrize("path", [DOCS, SCHEMA])
def test_the_pages_are_never_cached(staff_client: Client, path: str) -> None:
    response = staff_client.get(path)

    assert (
        "no-store" in response["Cache-Control"]
        or "no-cache" in response["Cache-Control"]
    )


def test_the_served_schema_is_the_committed_contract(staff_client: Client) -> None:
    response = staff_client.get(SCHEMA)

    assert response.status_code == 200
    assert response.content.decode() == COMMITTED_SCHEMA.read_text()


def test_the_schema_can_be_read_as_json(staff_client: Client) -> None:
    response = staff_client.get(
        SCHEMA, headers={"Accept": "application/vnd.oai.openapi+json"}
    )

    document = json.loads(response.content)
    assert response.status_code == 200
    assert "/api/v1/auth/sign-in/" in document["paths"]
    assert document["components"]["securitySchemes"]["bearerAuth"]["scheme"] == "bearer"


def test_the_docs_do_not_describe_themselves(staff_client: Client) -> None:
    document = json.loads(
        staff_client.get(
            SCHEMA, headers={"Accept": "application/vnd.oai.openapi+json"}
        ).content
    )

    assert not [
        path
        for path in document["paths"]
        if path.startswith("/api/docs") or path.startswith("/api/schema")
    ]


# --- never in production ----------------------------------------------------------------------


@pytest.mark.parametrize("environment", ["development", "staging"])
def test_development_and_staging_serve_the_docs(
    settings: Any, environment: str
) -> None:
    settings.APP_ENVIRONMENT = environment

    assert docs_enabled() is True
    assert [pattern.name for pattern in docs_urlpatterns()] == [
        "api-schema",
        "api-docs",
    ]


def test_production_serves_no_docs_route_at_all(settings: Any) -> None:
    settings.APP_ENVIRONMENT = "production"

    assert docs_enabled() is False
    assert docs_urlpatterns() == []


def test_an_unset_environment_on_the_production_settings_means_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The safe default: a deployment that forgets APP_ENVIRONMENT serves no docs."""
    monkeypatch.setenv("SECRET_KEY", "x" * 60)
    monkeypatch.delenv("APP_ENVIRONMENT", raising=False)

    production = importlib.reload(importlib.import_module("config.settings.production"))

    assert production.APP_ENVIRONMENT == "production"
