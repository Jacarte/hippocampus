"""Receipt namespaces follow storage targets, not connection credentials."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.memory_service import MemoryService


def namespace(provider="pgvector", **settings):
    return MemoryService._write_namespace({
        "vector_store": {"provider": provider, "config": settings},
    })


def test_postgres_dsn_overrides_individual_target_fields():
    first = namespace(
        connection_string="postgresql://worker:password@db:5432/first",
        host="ignored", dbname="ignored", user="ignored", collection_name="memories",
    )
    assert first == namespace(
        connection_string="host=db port=5432 dbname=first user=worker password=rotated",
        host="another-ignored-host", dbname="also-ignored", collection_name="memories",
    )
    assert first != namespace(
        connection_string="postgresql://worker:password@db:5432/second",
        collection_name="memories",
    )


def test_postgres_credentials_and_tuning_do_not_change_target():
    first = namespace(host="db", port=5432, dbname="app", user="worker", password="first")
    assert first == namespace(
        host="db", port="5432", dbname="app", user="worker", password="rotated",
        hnsw=True, diskann=False, embedding_model_dims=768, minconn=2, maxconn=20,
    )
    assert first == namespace(
        connection_string="postgresql://worker:rotated@db/app?sslmode=require&application_name=other",
    )
    assert first != namespace(host="db", port=5432, dbname="app", user="other")


@pytest.mark.parametrize("setting", ["search_path", "role", "session_authorization"])
def test_postgres_tenant_options_are_part_of_target(setting):
    base = "host=db dbname=app user=worker"
    first = namespace(connection_string=f"{base} options='-c {setting}=tenant_a'")
    assert first != namespace(connection_string=f"{base} options='-c {setting}=tenant_b'")
    assert first == namespace(
        connection_string=f"{base} options='-cstatement_timeout=1000 --{setting}=tenant_a'",
    )


def test_postgres_collection_remains_part_of_target():
    dsn = "host=db dbname=app user=worker"
    assert namespace(connection_string=dsn, collection_name="one") != namespace(
        connection_string=dsn, collection_name="two",
    )


def test_postgres_dsn_environment_target_defaults_are_preserved(monkeypatch):
    monkeypatch.setenv("PGDATABASE", "first")
    monkeypatch.setenv("PGPORT", "6432")
    monkeypatch.setenv("PGOPTIONS", "-c search_path=tenant_a")
    first = namespace(connection_string="host=db user=worker")
    assert first == namespace(
        connection_string="host=db user=worker dbname=first port=6432 options='-c search_path=tenant_a'",
    )
    monkeypatch.setenv("PGDATABASE", "second")
    assert first != namespace(connection_string="host=db user=worker")


@pytest.mark.parametrize("settings", [
    {"connection_pool": object()},
    {"client": object()},
    {"connection_string": "service=hidden-target"},
    {"connection_string": "dbname=app"},
])
def test_opaque_or_unresolved_postgres_targets_are_rejected(settings):
    with pytest.raises(ValueError, match="capture_id"):
        namespace(**settings)


def test_invalid_dsn_error_does_not_expose_credentials():
    with pytest.raises(ValueError) as error:
        namespace(connection_string="password='very-private")
    assert "very-private" not in str(error.value)


@pytest.mark.parametrize("key", ["url", "uri"])
def test_url_credential_rotation_preserves_target_but_database_changes_do_not(key):
    first = namespace("qdrant", **{
        key: "https://worker:first@EXAMPLE.com:443/api?token=one&database=app", "collection_name": "facts",
    })
    assert first == namespace("qdrant", **{
        key: "https://worker:rotated@example.com/api?database=app&token=two", "collection_name": "facts",
    })
    assert first != namespace("qdrant", **{
        key: "https://worker:rotated@example.com/api?database=other&token=two", "collection_name": "facts",
    })


def test_effective_model_configuration_and_opaque_clients():
    class EffectiveConfig:
        def model_dump(self, *, mode):
            assert mode == "python"
            return {"vector_store": {"provider": "qdrant", "config": {"client": object()}}}

    with pytest.raises(ValueError, match="opaque"):
        MemoryService._write_namespace(EffectiveConfig())


def test_unidentified_generic_target_is_rejected():
    with pytest.raises(ValueError, match="explicit"):
        namespace("unknown-provider", collection_name="facts")
