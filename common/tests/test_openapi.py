"""The two money types are named once in the OpenAPI document (ADR 0008), and only when something uses them."""

from typing import Any

from django.urls import path
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from common.money import AMOUNT_PATTERN, SIGNED_AMOUNT_PATTERN
from common.openapi import MARKER, name_money_types
from common.serializers import (
    AmountField,
    CurrencyField,
    MoneySerializerMixin,
    SignedAmountField,
)


class DemoItemSerializer(MoneySerializerMixin, serializers.Serializer):  # type: ignore[misc]
    amount = AmountField(allow_zero=False)
    balance = SignedAmountField(read_only=True, help_text="What is left.")
    currency = CurrencyField()


class DemoView(APIView):  # type: ignore[misc]
    @extend_schema(
        operation_id="demo_item",
        description="A throwaway operation that uses both money types.",
        request=DemoItemSerializer,
        responses={200: DemoItemSerializer},
    )
    def post(self, request: Any) -> Response:
        return Response({})


def schema() -> dict[str, Any]:
    generator = SchemaGenerator(patterns=[path("demo/", DemoView.as_view())])  # type: ignore[no-untyped-call]
    document: dict[str, Any] = generator.get_schema(request=None, public=True)  # type: ignore[no-untyped-call]
    return document


def test_both_types_are_shared_components_with_the_patterns_of_the_adr() -> None:
    components = schema()["components"]["schemas"]

    for name, pattern in (
        ("Amount", AMOUNT_PATTERN),
        ("SignedAmount", SIGNED_AMOUNT_PATTERN),
    ):
        assert components[name]["type"] == "string"
        assert components[name]["format"] == "decimal"
        assert components[name]["pattern"] == pattern
        assert components[name]["description"]


def test_a_field_refers_to_its_type_and_keeps_its_own_description() -> None:
    components = schema()["components"]["schemas"]

    response = components["DemoItem"]["properties"]
    request = components["DemoItemRequest"]["properties"]

    assert response["balance"]["allOf"] == [
        {"$ref": "#/components/schemas/SignedAmount"}
    ]
    assert response["balance"]["description"] == "What is left."
    assert response["amount"]["allOf"] == [{"$ref": "#/components/schemas/Amount"}]
    assert request["amount"]["allOf"] == [{"$ref": "#/components/schemas/Amount"}]


def test_no_marker_is_left_in_the_document() -> None:
    assert MARKER not in str(schema())


def test_nothing_is_added_when_no_amount_is_used() -> None:
    result = {"paths": {}, "components": {"schemas": {"Other": {"type": "object"}}}}

    named = name_money_types(result, None, None, True)

    assert set(named["components"]["schemas"]) == {"Other"}


def test_only_the_used_type_is_added() -> None:
    result: dict[str, Any] = {
        "paths": {"/x": {"properties": {"a": {"type": "string", MARKER: "Amount"}}}}
    }

    named = name_money_types(result, None, None, True)

    assert set(named["components"]["schemas"]) == {"Amount"}
    assert named["paths"]["/x"]["properties"]["a"] == {
        "$ref": "#/components/schemas/Amount"
    }
