import pytest
from django.test import override_settings
from drf_spectacular.generators import SchemaGenerator
from pytest_django.fixtures import Settings
from rest_framework.test import APIClient

from config.tests.isolation import assert_hidden_from_other_users
from users.models import User

BASE = "/api/v1/test"


@pytest.fixture(autouse=True)
def _test_urls(settings: Settings) -> None:
    settings.ROOT_URLCONF = "config.tests.urls"


# --- authentication and default permissions ---------------------------------------------------


def test_a_request_without_a_token_is_refused_with_401(api_client: APIClient) -> None:
    response = api_client.get(f"{BASE}/whoami/")

    assert response.status_code == 401
    assert response["WWW-Authenticate"] == "Bearer"
    assert response.json() == {
        "type": "client_error",
        "errors": [
            {
                "code": "not_authenticated",
                "detail": "Authentication credentials were not provided.",
                "attr": None,
            }
        ],
    }


def test_a_bearer_token_is_refused_until_tokens_can_be_issued(
    api_client: APIClient,
) -> None:
    response = api_client.get(
        f"{BASE}/whoami/", headers={"Authorization": "Bearer abc.def.ghi"}
    )

    assert response.status_code == 401
    assert response["WWW-Authenticate"] == "Bearer"
    assert response.json()["errors"][0]["code"] == "token_invalid"


def test_another_authorization_scheme_is_ignored(api_client: APIClient) -> None:
    response = api_client.get(
        f"{BASE}/whoami/", headers={"Authorization": "Basic dXNlcjpwYXNz"}
    )

    assert response.status_code == 401
    assert response.json()["errors"][0]["code"] == "not_authenticated"


def test_a_signed_in_user_is_let_in(auth_client: APIClient, user: User) -> None:
    response = auth_client.get(f"{BASE}/whoami/")

    assert response.status_code == 200
    assert response.json()["id"] == str(user.pk)


def test_a_view_can_opt_out_of_the_default_permission(api_client: APIClient) -> None:
    assert api_client.get(f"{BASE}/public/").status_code == 200


# --- the error body and headers ---------------------------------------------------------------


def test_a_validation_error_names_the_field(auth_client: APIClient) -> None:
    response = auth_client.post(f"{BASE}/invalid/", {})

    assert response.status_code == 400
    assert response.json() == {
        "type": "validation_error",
        "errors": [
            {"code": "invalid", "detail": "This field is required.", "attr": "name"}
        ],
    }


def test_a_conflict_is_a_409_with_its_own_code(auth_client: APIClient) -> None:
    response = auth_client.delete(f"{BASE}/conflict/")

    assert response.status_code == 409
    assert response.json() == {
        "type": "client_error",
        "errors": [
            {
                "code": "account_has_transactions",
                "detail": "The account has transactions.",
                "attr": None,
            }
        ],
    }


def test_a_throttled_answer_sends_retry_after_in_whole_seconds(
    api_client: APIClient,
) -> None:
    response = api_client.post(f"{BASE}/locked/")

    assert response.status_code == 429
    assert response["Retry-After"] == "42"
    assert response.json()["errors"][0]["code"] == "sign_in_locked"


def test_the_api_speaks_json_only(auth_client: APIClient) -> None:
    response = auth_client.get(f"{BASE}/whoami/", headers={"Accept": "text/html"})

    assert response.status_code == 406


# --- owner scoping ----------------------------------------------------------------------------


def test_a_user_reads_their_own_row(auth_client: APIClient, user: User) -> None:
    response = auth_client.get(f"{BASE}/users/{user.pk}/")

    assert response.status_code == 200
    assert response.json() == {"id": str(user.pk)}


def test_another_users_row_answers_404_for_every_method(
    auth_client: APIClient, other_user: User
) -> None:
    assert_hidden_from_other_users(auth_client, f"{BASE}/users/{other_user.pk}/")
    assert User.objects.filter(pk=other_user.pk).exists()


@pytest.mark.parametrize("method", ["get", "put", "patch", "delete"])
def test_the_isolation_helper_fails_when_a_row_is_visible(
    auth_client: APIClient, user: User, method: str
) -> None:
    with pytest.raises(AssertionError, match="expected 404"):
        assert_hidden_from_other_users(
            auth_client, f"{BASE}/users/{user.pk}/", methods=[method]
        )
    # a DELETE above removed the signed-in user: nothing else to clean up in this test database
    User.objects.filter(pk=user.pk).delete()


# --- the contract -----------------------------------------------------------------------------


@override_settings(ROOT_URLCONF="config.tests.urls")
def test_the_schema_declares_the_bearer_scheme_and_documents_409_only_where_declared() -> (
    None
):
    # drf-spectacular is only partly typed
    generator = SchemaGenerator()  # type: ignore[no-untyped-call]
    schema = generator.get_schema(request=None, public=True)  # type: ignore[no-untyped-call]

    assert schema["components"]["securitySchemes"]["bearerAuth"] == {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
    }
    paths = schema["paths"]
    assert "409" in paths["/api/v1/test/conflict/"]["delete"]["responses"]
    assert "409" not in paths["/api/v1/test/whoami/"]["get"]["responses"]
    assert "401" in paths["/api/v1/test/whoami/"]["get"]["responses"]
    assert "401" not in paths["/api/v1/test/public/"]["get"]["responses"]
