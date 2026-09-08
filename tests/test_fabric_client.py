"""Offline API contract and security tests. No real tokens or cloud writes."""

import json

import httpx
import pytest

from fabric.fabric_client import (
    AmbiguousWrite,
    FabricClient,
    FabricError,
    FabricTimeout,
    operation_url,
    retry_after,
    trusted_url,
)


class Clock:
    def __init__(self):
        self.now = 0.0
        self.waits = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds


def client(handler, **kwargs):
    return FabricClient(lambda: "local-test-token", role="provider",
                        transport=httpx.MockTransport(handler), **kwargs)


@pytest.mark.parametrize("url", [
    "https://evil.example/v1/workspaces", "http://api.fabric.microsoft.com/v1/workspaces",
    "https://api.fabric.microsoft.com.evil.example/v1/workspaces",
    "https://user@api.fabric.microsoft.com/v1/workspaces", "//evil.example/v1/workspaces",
    "https://api.fabric.microsoft.com:444/v1/workspaces",
    "https://api.fabric.microsoft.com/v1/%2e%2e/secret", "https://api.fabric.microsoft.com/v1/x#fragment",
    "https://api.fabric.microsoft.com\\@evil.example/v1/x",
])
def test_untrusted_host_rejected_before_token(url):
    calls = []
    api = FabricClient(lambda: calls.append("token") or "token", role="provider")
    try:
        with pytest.raises(FabricError, match="Untrusted"):
            api.get(url)
        assert calls == []
    finally:
        api.close()


def test_relative_trusted_url():
    assert trusted_url("workspaces") == "https://api.fabric.microsoft.com/v1/workspaces"


@pytest.mark.parametrize("error_code", ["OperationHasNoResult", "InvalidRequest"])
def test_succeeded_operation_without_result(error_code):
    def handler(request):
        if request.url.path.endswith("/result"):
            return httpx.Response(400, json={"errorCode": error_code})
        return httpx.Response(200, json={"status": "Succeeded"})

    clock = Clock()
    api = client(handler, sleep=clock.sleep, clock=clock)
    response = httpx.Response(202, headers={"Location": "https://api.fabric.microsoft.com/v1/operations/demo"})
    try:
        if error_code == "OperationHasNoResult":
            assert api.poll(response) == {}
        else:
            with pytest.raises(FabricError, match="result failed"):
                api.poll(response)
    finally:
        api.close()


def test_pagination_uri_and_token():
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(200, json={"value": [{"id": "one"}], "continuationToken": "a+/="})
        if len(requests) == 2:
            assert request.url.params["continuationToken"] == "a+/="
            return httpx.Response(200, json={"value": [{"id": "two"}],
                                  "continuationUri": "https://api.fabric.microsoft.com/v1/capacities?continuationToken=b"})
        return httpx.Response(200, json={"value": [{"id": "three"}]})

    api = client(handler)
    try:
        assert api.list("capacities") == [{"id": "one"}, {"id": "two"}, {"id": "three"}]
    finally:
        api.close()


def test_pagination_no_cross_host_token_leak():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"value": [], "continuationUri": "https://evil.example/v1/x"})

    api = client(handler)
    try:
        with pytest.raises(FabricError, match="Untrusted"):
            api.list("workspaces")
        assert len(calls) == 1
    finally:
        api.close()


def test_pagination_cycle():
    api = client(lambda r: httpx.Response(200, json={"value": [], "continuationUri": str(r.url)}))
    try:
        with pytest.raises(FabricError, match="cycle"):
            api.list("workspaces")
    finally:
        api.close()


def test_get_retry_after_and_post_no_5xx_retry():
    clock = Clock()
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(200, json={"value": []})

    api = client(handler, sleep=clock.sleep, clock=clock)
    try:
        assert api.list("capacities") == []
        assert clock.waits == [7]
        assert len(calls) == 2
    finally:
        api.close()
    calls.clear()
    api = client(lambda r: calls.append(r) or httpx.Response(503))
    try:
        with pytest.raises(AmbiguousWrite):
            api.mutate("POST", "workspaces", {"displayName": "demo"})
        assert len(calls) == 1
    finally:
        api.close()


def test_write_transport_failure_not_replayed():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("test", request=request)

    api = client(handler)
    try:
        with pytest.raises(AmbiguousWrite):
            api.request("POST", "workspaces")
        assert len(calls) == 1
    finally:
        api.close()


def test_bounded_retry_after():
    clock = Clock()
    api = client(lambda r: httpx.Response(429, headers={"Retry-After": "100"}),
                 timeout=10, clock=clock, sleep=clock.sleep)
    try:
        with pytest.raises(FabricTimeout):
            api.get("workspaces")
        assert not clock.waits
    finally:
        api.close()


def test_lro_waits_and_fetches_result_not_operation_id():
    clock = Clock()
    calls = []

    def handler(request):
        calls.append(request)
        if request.method == "POST":
            return httpx.Response(202, headers={"Location": "/v1/operations/op", "Retry-After": "2"})
        if str(request.url).endswith("/result"):
            return httpx.Response(200, json={"id": "live-result"})
        if len(calls) == 2:
            return httpx.Response(200, json={"status": "Running"}, headers={"Retry-After": "3"})
        return httpx.Response(200, json={"status": "Succeeded"},
                              headers={"Location": "/v1/operations/op/result"})

    api = client(handler, sleep=clock.sleep, clock=clock)
    try:
        assert api.mutate("POST", "workspaces/ws/lakehouses") == {"id": "live-result"}
        assert clock.waits == [2, 3]
    finally:
        api.close()


@pytest.mark.parametrize("status", ["Failed", "Cancelled", "Deduped", "FutureStatus"])
def test_job_failures_propagate(status):
    clock = Clock()

    def handler(request):
        if request.method == "POST":
            assert request.url.path.endswith("/items/nb/jobs/RunNotebook/instances")
            assert not request.content  # no invented executionData parameter schema
            return httpx.Response(202, headers={"Location": "/v1/workspaces/ws/items/nb/jobs/instances/job"})
        return httpx.Response(200, json={"status": status})

    api = client(handler, sleep=clock.sleep, clock=clock)
    try:
        with pytest.raises(FabricError):
            api.run_notebook("ws", "nb")
    finally:
        api.close()


def test_job_poll_timeout():
    clock = Clock()
    api = client(lambda r: httpx.Response(200, json={"status": "InProgress"}),
                 clock=clock, sleep=clock.sleep, operation_timeout=7)
    try:
        with pytest.raises(FabricTimeout):
            api.poll(httpx.Response(202, headers={"Location": "/v1/jobs/job", "Retry-After": "1"}), job=True)
        assert clock.now <= 7
    finally:
        api.close()


def test_hostile_location_and_redirect_not_followed():
    calls = []
    api = client(lambda r: calls.append(r) or httpx.Response(202, headers={"Location": "https://evil.example/v1/x"}))
    try:
        with pytest.raises(FabricError, match="Untrusted"):
            api.mutate("POST", "workspaces")
        assert len(calls) == 1
    finally:
        api.close()
    api = client(lambda r: httpx.Response(302, headers={"Location": "/v1/elsewhere"}))
    try:
        with pytest.raises(FabricError, match="302"):
            api.get("workspaces")
    finally:
        api.close()


def test_operation_id_takes_precedence_over_untrusted_location():
    operation_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    calls = []

    def handler(request):
        calls.append(request)
        if request.method == "POST":
            return httpx.Response(202, headers={
                "Location": "https://regional.example/operation",
                "x-ms-operation-id": operation_id,
                "Retry-After": "0",
            })
        if request.url.path.endswith("/result"):
            return httpx.Response(200, json={"id": "created"})
        return httpx.Response(200, headers={"Location": "https://evil.example/result"},
                              json={"status": "Succeeded"})

    api = client(handler)
    try:
        assert api.mutate("POST", "workspaces") == {"id": "created"}
        assert calls[1].url.path == f"/v1/operations/{operation_id}"
    finally:
        api.close()


def test_regional_job_location_is_rebuilt_on_trusted_api_host():
    workspace_id = "11111111-1111-4111-8111-111111111111"
    item_id = "22222222-2222-4222-8222-222222222222"
    job_id = "33333333-3333-4333-8333-333333333333"
    response = httpx.Response(202, headers={
        "Location": (f"https://regional.example/v1/workspaces/{workspace_id}/items/{item_id}"
                     f"/jobs/instances/{job_id}"),
    })
    assert operation_url(response) == (
        f"https://api.fabric.microsoft.com/v1/workspaces/{workspace_id}/items/{item_id}"
        f"/jobs/instances/{job_id}"
    )


def test_completed_create_ignores_unused_regional_location():
    api = client(lambda request: httpx.Response(
        201,
        headers={"Location": "https://regional.example/resource"},
        json={"id": "11111111-1111-4111-8111-111111111111"},
    ))
    try:
        response = api.request("POST", "workspaces", body={"displayName": "demo"})
        assert response.status_code == 201
    finally:
        api.close()


def test_dry_client_never_acquires_token():
    calls = []
    api = FabricClient(lambda: calls.append(1) or "token", role="provider", dry_run=True)
    try:
        with pytest.raises(FabricError, match="dry-run"):
            api.get("workspaces")
        assert not calls
    finally:
        api.close()


def test_logs_do_not_include_token_body_or_query(caplog):
    api = client(lambda r: httpx.Response(400, json={"message": "sensitive-body"},
                 headers={"request-id": "malicious-sensitive-header"}))
    try:
        with caplog.at_level("INFO", logger="elite.fabric"):
            with pytest.raises(FabricError):
                api.request("POST", "workspaces?secret=private-query", body={"secret": "private-body"})
        assert "local-test-token" not in caplog.text
        assert "private-" not in caplog.text
        assert "sensitive" not in caplog.text
        assert json.loads(caplog.records[-1].message)["status"] == 400
    finally:
        api.close()


def test_invalid_retry_after_fails_closed():
    with pytest.raises(FabricError, match="Retry-After"):
        retry_after(httpx.Response(429, headers={"Retry-After": "nan"}))