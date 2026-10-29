from pathlib import Path
from types import SimpleNamespace

import pytest

from mishkan.config.loader import ConfigLoader
from mishkan.config.models import (
    CredentialReference,
    CredentialSource,
    ProviderConfig,
)
from mishkan.crewai.credentials import CredentialPoolResolver
from mishkan.crewai.routing import CrewAIModelRouter
from mishkan.domain.errors import ErrorCode, MishkanError


def _reference(source: CredentialSource, locator: str) -> CredentialReference:
    return CredentialReference(source=source, locator=locator)


def test_credential_pool_resolves_each_public_reference_type(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    secret_file = tmp_path / "provider.token"
    secret_file.write_text("file-secret\n", encoding="utf-8")
    monkeypatch.setenv("MISHKAN_TEST_API_KEY", "environment-secret")
    monkeypatch.setattr(
        "mishkan.crewai.credentials.importlib.import_module",
        lambda _name: SimpleNamespace(get_password=lambda _service, _user: "keyring-secret"),
    )
    references = (
        _reference(CredentialSource.ENV, "MISHKAN_TEST_API_KEY"),
        _reference(CredentialSource.FILE, str(secret_file)),
        _reference(CredentialSource.COMMAND, "printf command-secret"),
        _reference(CredentialSource.KEYRING, "mishkan:test-user"),
    )

    assert CredentialPoolResolver().resolve(references) == (
        "environment-secret",
        "file-secret",
        "command-secret",
        "keyring-secret",
    )
    assert CredentialPoolResolver().resolve(()) == (None,)


def test_unresolvable_pool_reports_sources_without_secret_locators(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("MISHKAN_MISSING_API_KEY", raising=False)
    reference = _reference(CredentialSource.ENV, "MISHKAN_MISSING_API_KEY")

    with pytest.raises(MishkanError) as caught:
        CredentialPoolResolver().resolve((reference,))

    assert caught.value.envelope.code is ErrorCode.CONFIGURATION
    assert caught.value.envelope.details == {"sources": ["env"]}
    assert "MISHKAN_MISSING_API_KEY" not in str(caught.value.envelope.details)


def test_model_router_refuses_missing_routes_and_unsupported_providers() -> None:
    config = ConfigLoader().load([Path("tests/fixtures/config/local-valid.yaml")]).value
    router = CrewAIModelRouter(config)

    with pytest.raises(MishkanError) as missing:
        next(router.candidates_for("missing"))
    assert missing.value.envelope.code is ErrorCode.CONFIGURATION

    original = config.providers["local-models"]
    unsupported = ProviderConfig(
        kind="unsupported-provider",
        endpoint=original.endpoint,
    )
    changed = config.model_copy(update={"providers": {"local-models": unsupported}})
    with pytest.raises(MishkanError) as unknown:
        next(CrewAIModelRouter(changed).candidates_for("planning"))
    assert unknown.value.envelope.details == {"kind": "unsupported-provider"}


def test_model_router_applies_a_stricter_task_timeout() -> None:
    config = ConfigLoader().load([Path("tests/fixtures/config/local-valid.yaml")]).value

    llm = next(CrewAIModelRouter(config).candidates_for("planning", timeout_seconds=11))

    assert llm.timeout == 11
    with pytest.raises(MishkanError):
        next(CrewAIModelRouter(config).candidates_for("planning", timeout_seconds=0))
