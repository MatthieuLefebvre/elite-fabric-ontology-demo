"""Offline API contract and security tests. No real tokens or cloud writes."""

import json

import httpx
import pytest

from fabric.fabric_client import (
    AmbiguousWrite,
    FabricClient,
    FabricError,
    FabricTimeout,
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
            api.request("POST", "workspaces")
        assert len(calls) == 1
    finally:
        api.close()
    api = client(lambda r: httpx.Response(302, headers={"Location": "/v1/elsewhere"}))
    try:
        with pytest.raises(FabricError, match="302"):
            api.get("workspaces")
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