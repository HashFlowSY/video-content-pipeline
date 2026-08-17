from __future__ import annotations

import pytest

from video_content_pipeline.url_policy import (
    ManualCollectionSession,
    URLAccessMode,
    URLPolicyError,
    authorize_public_url,
    validate_destination,
)


def test_access_mode_is_runtime_mandatory() -> None:
    with pytest.raises(URLPolicyError) as error:
        authorize_public_url("https://example.test/media", None)  # type: ignore[arg-type]

    assert error.value.reason == "url_mode_invalid"


def test_https_authorization_redacts_query_and_fragment() -> None:
    authorization = authorize_public_url(
        "https://example.test/watch/1?token=secret#fragment", URLAccessMode.FILTERED
    )

    assert authorization.provenance.as_json() == {
        "scheme": "https",
        "host": "example.test",
        "path": "/watch/1",
        "transport_integrity_verified": True,
    }
    assert "secret" not in str(authorization.provenance.as_json())


def test_http_needs_explicit_authorization() -> None:
    with pytest.raises(URLPolicyError) as error:
        authorize_public_url("http://example.test/media", URLAccessMode.DIRECT)

    assert error.value.reason == "insecure_http_not_authorized"


def test_explicitly_authorized_http_records_unverified_transport() -> None:
    authorization = authorize_public_url(
        "http://example.test/media", URLAccessMode.DIRECT, allow_insecure_http=True
    )

    assert authorization.provenance.transport_integrity_verified is False


def test_new_host_and_https_downgrade_are_not_implicit() -> None:
    authorization = authorize_public_url("https://example.test/media", URLAccessMode.DIRECT)
    with pytest.raises(URLPolicyError) as host_error:
        validate_destination(authorization, "https://cdn.example.test/media")
    with pytest.raises(URLPolicyError) as downgrade_error:
        validate_destination(authorization, "http://example.test/media")

    assert host_error.value.reason == "host_escalation"
    assert downgrade_error.value.reason == "https_downgrade"


def test_confirmed_media_hosts_are_admitted_for_one_validation_only() -> None:
    authorization = authorize_public_url("https://example.test/watch/1", URLAccessMode.DIRECT)
    confirmed = frozenset({"cdn-a.fake.test", "cdn-b.fake.test"})

    validate_destination(
        authorization, "https://cdn-a.fake.test/video.m4s", confirmed_media_hosts=confirmed
    )
    validate_destination(
        authorization, "https://cdn-b.fake.test/audio.m4s", confirmed_media_hosts=confirmed
    )
    validate_destination(
        authorization, "https://example.test/watch/1", confirmed_media_hosts=confirmed
    )


def test_undisclosed_host_is_escalation_even_with_a_confirmed_set() -> None:
    authorization = authorize_public_url("https://example.test/watch/1", URLAccessMode.DIRECT)
    confirmed = frozenset({"cdn-a.fake.test"})

    with pytest.raises(URLPolicyError) as error:
        validate_destination(
            authorization, "https://undisclosed.fake.test/media", confirmed_media_hosts=confirmed
        )

    assert error.value.reason == "host_escalation"


def test_confirmed_media_hosts_never_relax_transport_integrity() -> None:
    authorization = authorize_public_url("https://example.test/watch/1", URLAccessMode.DIRECT)

    with pytest.raises(URLPolicyError) as error:
        validate_destination(
            authorization,
            "http://cdn-a.fake.test/video.m4s",
            confirmed_media_hosts=frozenset({"cdn-a.fake.test"}),
        )

    assert error.value.reason == "https_downgrade"


def test_manual_collection_is_ordered_and_requires_endsignal() -> None:
    collection = ManualCollectionSession(mode=URLAccessMode.FILTERED)
    collection.append("https://example.test/part-1")
    collection.append("https://example.test/part-2")

    with pytest.raises(URLPolicyError) as close_error:
        collection.close("done")
    assert close_error.value.reason == "collection_closure_required"

    assert [entry.provenance.path for entry in collection.close("结束")] == ["/part-1", "/part-2"]


def test_manual_collection_rejects_duplicate_raw_url() -> None:
    collection = ManualCollectionSession(mode=URLAccessMode.DIRECT)
    collection.append("https://example.test/part-1")
    with pytest.raises(URLPolicyError) as error:
        collection.append("https://example.test/part-1")

    assert error.value.reason == "duplicate_url"


def test_manual_collection_consumes_insecure_http_authorization_per_entry() -> None:
    collection = ManualCollectionSession(mode=URLAccessMode.DIRECT, allow_insecure_http=True)
    collection.append("http://example.test/part-1")

    with pytest.raises(URLPolicyError) as error:
        collection.append("http://example.test/part-2")

    assert error.value.reason == "insecure_http_not_authorized"
