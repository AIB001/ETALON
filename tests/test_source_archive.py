"""Exact pre-parser transport capture; fixtures are not live scientific evidence."""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from etalon.boundary.quarry import DataBudget, Quarry
from etalon.data.artifacts import digest, new_run, read_snapshot, seal, write_json
from etalon.data.service import run_data
from etalon.data.source_archive import INDEX, public_descriptor, source_archive_status

REQUEST = {"kind": "query", "source": "chembl", "operation": "molecule",
           "parameters": {"chembl_id": "CHEMBL25"}}
BODY = b'{  "molecule_chembl_id": "CHEMBL25", "scientific_value": 1.2500 }\n'


def archive(snapshot):
    root = Path(snapshot["snapshot"])
    return root, json.loads((root / INDEX).read_text(encoding="utf-8"))


def test_original_bytes_normalized_page_link_and_tamper_detection(tmp_path):
    acquired = run_data(REQUEST, tmp_path, run_id="original", transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=BODY, headers={"content-type": "application/json"})))
    root, index = archive(acquired)
    capture = index["attempts"][0]
    assert (root / capture["body"]["path"]).read_bytes() == BODY
    assert capture["body"]["sha256"] == hashlib.sha256(BODY).hexdigest()
    assert capture["body"]["complete"] is True
    assert capture["body"]["digest_scope"] == "http_content_encoded_body"
    page = json.loads((root / "payload/page-0001.json").read_text())
    assert page["records"][0]["scientific_value"] == 1.25
    assert capture["provenance"]["response_sha256"] == capture["body"]["sha256"]
    assert index["normalized_pages"][0]["response_links"][0]["capture_ids"] == [capture["capture_id"]]
    assert acquired["source_archive"]["coverage"]["complete_bodies"] == 1
    assert source_archive_status(read_snapshot(root))["status"] == "captured"
    body_path = root / capture["body"]["path"]
    body_path.write_bytes(BODY + b"changed")
    with pytest.raises(ValueError, match="file changed"):
        read_snapshot(root)


@pytest.mark.parametrize("buffered", [False, True])
def test_compression_digest_scopes_do_not_replace_original_provenance(tmp_path, buffered):
    encoded = gzip.compress(BODY, mtime=0)

    def response(request):
        kwargs = {"content": encoded} if buffered else {"stream": httpx.ByteStream(encoded)}
        return httpx.Response(200, headers={"content-encoding": "gzip",
            "content-type": "application/json", "content-length": str(len(encoded))}, **kwargs)

    acquired = run_data(REQUEST, tmp_path, run_id="gzip", transport=httpx.MockTransport(response))
    root, index = archive(acquired)
    capture = index["attempts"][0]
    body = capture["body"]
    assert body["content_encoding"] == "gzip"
    assert body["encoded_original_available"] is not buffered
    expected = BODY if buffered else encoded
    assert (root / body["path"]).read_bytes() == expected
    assert body["sha256"] == hashlib.sha256(expected).hexdigest()
    assert capture["provenance"]["response_sha256"] == hashlib.sha256(BODY).hexdigest()
    assert capture["provenance_digest_scope"] == "http_content_decoded_body"
    assert body["complete"]


@pytest.mark.parametrize("status,body,code", [(404, b'{"error":"missing"}', "not_found"),
    (200, b"not JSON\n", "invalid_response")])
def test_http_error_and_parse_failure_still_seal_actual_response(tmp_path, status, body, code):
    acquired = run_data(REQUEST, tmp_path, run_id="failed-response", transport=httpx.MockTransport(
        lambda request: httpx.Response(status, stream=httpx.ByteStream(body))))
    root, index = archive(acquired)
    assert acquired["result"]["status"] == "partial"
    assert acquired["result"]["errors"][0]["code"] == code
    assert (root / index["attempts"][0]["body"]["path"]).read_bytes() == body
    assert index["attempts"][0]["status_code"] == status
    assert index["operations"][0]["outcome"] == "error"
    read_snapshot(root)


def test_retry_attempts_source_version_allowed_headers_and_exact_bytes(tmp_path):
    calls = []

    def response(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503, content=b"retry me", headers={"retry-after": "0"})
        return httpx.Response(200, content=BODY, headers={"x-uniprot-release": "fixture-release",
            "set-cookie": "credential=secret", "etag": '"public-etag"'})

    acquired = run_data(REQUEST, tmp_path, run_id="retry", transport=httpx.MockTransport(response))
    root, index = archive(acquired)
    assert [item["status_code"] for item in index["attempts"]] == [503, 200]
    assert [item["body"]["complete"] for item in index["attempts"]] == [True, True]
    assert index["attempts"][1]["source_version"] == "fixture-release"
    assert "set-cookie" not in index["attempts"][1]["response_headers"]
    assert (root / index["attempts"][0]["body"]["path"]).read_bytes() == b"retry me"


def test_pagination_links_every_page_and_marks_truncation(tmp_path):
    def response(request):
        offset = int(request.url.params.get("offset", "0"))
        return httpx.Response(200, json={"molecules": [{"id": str(offset), "smiles": "CCO"}],
            "page_meta": {"total_count": 3, "offset": offset, "limit": 1, "next": "more"}})

    acquired = run_data({"kind": "query", "source": "chembl", "operation": "search_molecules",
        "parameters": {"query": "fixture", "limit": 1}, "max_pages": 2}, tmp_path,
        run_id="pages", transport=httpx.MockTransport(response))
    root, index = archive(acquired)
    assert acquired["result"]["status"] == "partial"
    assert acquired["result"]["next_parameters"]["offset"] == 2
    assert len(index["attempts"]) == len(index["normalized_pages"]) == 2
    assert index["normalized_pages"][0]["response_links"][0]["capture_ids"] == ["http-000001"]
    assert index["normalized_pages"][1]["response_links"][0]["capture_ids"] == ["http-000002"]
    read_snapshot(root)


def test_initial_public_pagination_cursor_is_retained_at_every_request_boundary(tmp_path):
    cursor = "PUBLIC-NONSECRET-PAGE-CURSOR-123+/="
    actual_requests = []

    def transport(request):
        actual_requests.append(request)
        return httpx.Response(200, json={"studies": [], "totalCount": 0})

    acquired = run_data({"kind": "query", "source": "clinicaltrials", "operation": "search",
        "parameters": {"query": "kinase", "page_token": cursor}}, tmp_path,
        run_id="initial-cursor", transport=httpx.MockTransport(transport))
    root, index = archive(acquired)
    assert actual_requests[0].url.params["pageToken"] == cursor
    saved_request = json.loads((root / "request.json").read_text())
    saved_page = json.loads((root / "payload/page-0001.json").read_text())
    assert saved_request["request"]["parameters"]["page_token"] == cursor
    assert saved_page["parameters"]["page_token"] == cursor
    assert index["operations"][0]["request"]["parameters"]["pageToken"] == cursor
    assert httpx.URL(index["attempts"][0]["request"]["url"]).params["pageToken"] == cursor
    assert httpx.URL(saved_page["provenance"][0]["url"]).params["pageToken"] == cursor
    # This is an initial non-first-page request: no earlier response can recover its cursor.
    assert saved_page["next_parameters"] is None
    read_snapshot(root)


def test_public_pagination_exception_keeps_authentication_tokens_redacted():
    descriptor = public_descriptor({"page_token": "public-snake-cursor", "pageToken": "public-camel-cursor",
        "access_token": "secret-access", "idToken": "secret-id", "refresh_token": "secret-refresh",
        "token": "secret-generic", "apiToken": "secret-api", "client_secret": "secret-client"})
    assert descriptor["page_token"] == "public-snake-cursor"
    assert descriptor["pageToken"] == "public-camel-cursor"
    assert all(value == "REDACTED" for key, value in descriptor.items()
               if key not in {"page_token", "pageToken"})


def test_budget_overrun_retains_actual_prefix_and_unsealed_failure(tmp_path):
    with pytest.raises(Exception, match="max_bytes"):
        run_data({"kind": "download", "source": "chembl", "operation": "sdf",
                  "parameters": {"chembl_id": "CHEMBL25"}}, tmp_path, run_id="overrun",
            budget=DataBudget(max_bytes=10), transport=httpx.MockTransport(
                lambda request: httpx.Response(200, stream=httpx.ByteStream(b"x" * 20))))
    root = tmp_path / "data/overrun"
    index = json.loads((root / INDEX).read_text())
    body = index["attempts"][0]["body"]
    assert body["bytes"] == 20 and body["complete"] is False
    assert (root / body["path"]).read_bytes() == b"x" * 20
    assert not (root / "snapshot.json").exists()


def test_download_error_body_is_captured_when_consumer_rejects_headers(tmp_path):
    with pytest.raises(Exception, match="HTTP 404"):
        run_data({"kind": "download", "source": "chembl", "operation": "sdf",
                  "parameters": {"chembl_id": "CHEMBL25"}}, tmp_path, run_id="download-404",
            transport=httpx.MockTransport(lambda request: httpx.Response(404,
                stream=httpx.ByteStream(b"source says missing"))))
    root = tmp_path / "data/download-404"
    index = json.loads((root / INDEX).read_text())
    body = index["attempts"][0]["body"]
    assert body["complete"]
    assert (root / body["path"]).read_bytes() == b"source says missing"
    assert json.loads((root / "failure.json").read_text())["usage"]["response_bytes"] == body["bytes"]


def test_redirect_body_and_final_response_are_separate_attempts(tmp_path):
    def transport(request):
        if request.url.path == "/first":
            return httpx.Response(302, headers={"location": "https://www.ebi.ac.uk/final"},
                                  stream=httpx.ByteStream(b"redirect response"))
        return httpx.Response(200, stream=httpx.ByteStream(BODY))

    with Quarry(tmp_path / "home", DataBudget(), archive_root=tmp_path / "redirect",
                transport=httpx.MockTransport(transport)) as quarry:
        from molquarry.catalog import SOURCES

        result = quarry.client.http.text(SOURCES["chembl"], "GET", "https://www.ebi.ac.uk/first",
                                        redirect_hosts=frozenset({"www.ebi.ac.uk"}))
        assert result.data == BODY.decode()
    root = tmp_path / "redirect"
    index = json.loads((root / INDEX).read_text())
    assert [item["status_code"] for item in index["attempts"]] == [302, 200]
    assert [item["body"]["complete"] for item in index["attempts"]] == [True, True]
    assert (root / index["attempts"][0]["body"]["path"]).read_bytes() == b"redirect response"


def test_interrupted_stream_remains_an_explicit_prefix(tmp_path):
    class Broken(httpx.SyncByteStream):
        def __iter__(self):
            yield b'{"partial":'
            raise httpx.ReadError("fixture interruption")

    with Quarry(tmp_path / "home", DataBudget(), archive_root=tmp_path / "capture",
                transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Broken()))) as quarry:
        quarry.client.http.max_attempts = 1
        with pytest.raises(Exception, match="ReadError"):
            quarry.client.query("chembl", "molecule", chembl_id="CHEMBL25")
    root = tmp_path / "capture"
    index = json.loads((root / INDEX).read_text())
    body = index["attempts"][0]["body"]
    assert (root / body["path"]).read_bytes() == b'{"partial":'
    assert body["complete"] is False
    assert index["attempts"][0]["status_code"] == 200


@pytest.mark.parametrize("new_archive", [False, True])
def test_cache_never_invents_original_from_parsed_data(tmp_path, new_archive):
    with Quarry(tmp_path / "home", DataBudget(), archive_root=tmp_path / "first",
                transport=httpx.MockTransport(lambda request: httpx.Response(200, content=BODY))) as quarry:
        quarry.client.http.cache_dir = tmp_path / "exploratory-cache"
        quarry.client.query("chembl", "molecule", chembl_id="CHEMBL25")
        if not new_archive:
            quarry.client.query("chembl", "molecule", chembl_id="CHEMBL25")
            assert quarry.usage()["requests"] == 1
    if new_archive:
        with Quarry(tmp_path / "home", DataBudget(max_requests=0), archive_root=tmp_path / "second",
                    transport=httpx.MockTransport(lambda request: pytest.fail("must be cached"))) as quarry:
            quarry.client.http.cache_dir = tmp_path / "exploratory-cache"
            quarry.client.query("chembl", "molecule", chembl_id="CHEMBL25")
            assert quarry.usage()["requests"] == 0
    root = tmp_path / ("second" if new_archive else "first")
    index = json.loads((root / INDEX).read_text())
    hit = index["operations"][-1]
    assert hit["origin"] == "cache"
    assert hit["original_availability"] == ("original_unavailable" if new_archive else
                                             "original_available_in_snapshot")
    assert len(index["attempts"]) == (0 if new_archive else 1)
    assert len(list((root / "source-archive/bodies").iterdir())) == (0 if new_archive else 1)


def test_sanitization_preserves_science_and_excludes_private_token_response(tmp_path):
    public = public_descriptor({"url": "https://user:password@example.org/data?q=kinase&api_key=secret#token",
        "parameters": {"query": "kinase", "access_token": "secret"}, "Kd_nM": 2.5})
    encoded = json.dumps(public)
    assert "password" not in encoded and "secret" not in encoded
    assert public["Kd_nM"] == 2.5 and public["parameters"]["query"] == "kinase"
    with Quarry(tmp_path / "home", DataBudget(), archive_root=tmp_path / "private",
                transport=httpx.MockTransport(lambda request: httpx.Response(200,
                    json={"access_token": "RESPONSE-TOKEN"}))) as quarry:
        from molquarry.catalog import SOURCES

        quarry.client.http.json(SOURCES["chembl"], "POST", "https://example.org/token?api_key=URL-KEY",
            form={"client_secret": "FORM-SECRET"}, private=True,
            headers={"Authorization": "Bearer HEADER-TOKEN"})
    root = tmp_path / "private"
    encoded = (root / INDEX).read_text()
    for secret in ("RESPONSE-TOKEN", "URL-KEY", "FORM-SECRET", "HEADER-TOKEN"):
        assert secret not in encoded
    assert not list((root / "source-archive/bodies").iterdir())
    assert json.loads(encoded)["attempts"][0]["body_availability"] == "omitted_private_response"


def test_old_snapshot_seal_and_identity_remain_unchanged(tmp_path):
    root = new_run(tmp_path, "legacy", {"fixture": True})
    write_json(root / "normalized.json", {"scientific_value": 12.5})
    original = seal(root, kind="query", result={"status": "complete"}, infrastructure={"fixture": True})
    manifest = (root / "snapshot.json").read_bytes()
    loaded = read_snapshot(root)
    assert loaded["snapshot_id"] == original["snapshot_id"]
    assert source_archive_status(loaded)["status"] == "legacy_normalized_only"
    assert (root / "snapshot.json").read_bytes() == manifest
    body = json.loads(manifest)
    identity = body.pop("snapshot_id")
    assert digest(body) == identity
