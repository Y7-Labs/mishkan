"""Resolve public MISHKAN routes into supported CrewAI LLM objects."""

from collections.abc import Iterator

from crewai import LLM

from mishkan.config.models import MishkanConfig, ModelCandidate, ProviderConfig
from mishkan.crewai.credentials import CredentialPoolResolver
from mishkan.domain.errors import ErrorCode, MishkanError


class CrewAIModelRouter:
    """Materialize configured candidates; CrewAI remains the inference caller."""

    def __init__(
        self,
        config: MishkanConfig,
        credential_resolver: CredentialPoolResolver | None = None,
    ) -> None:
        self._config = config
        self._credentials = credential_resolver or CredentialPoolResolver()

    def candidates_for(
        self,
        route_name: str,
        *,
        timeout_seconds: int | None = None,
    ) -> Iterator[LLM]:
        route = self._config.model_routes.get(route_name)
        if route is None:
            raise MishkanError(
                ErrorCode.CONFIGURATION,
                "CrewAI model route does not exist",
                details={"route": route_name},
            )
        for candidate in route.candidates:
            provider = self._config.providers[candidate.provider]
            for credential in self._credentials.resolve(provider.credential_pool):
                yield self._materialize(
                    candidate,
                    provider,
                    credential,
                    timeout_seconds=timeout_seconds,
                )

    def _materialize(
        self,
        candidate: ModelCandidate,
        provider: ProviderConfig,
        credential: str | None,
        *,
        timeout_seconds: int | None = None,
    ) -> LLM:
        if timeout_seconds is not None and timeout_seconds < 1:
            raise MishkanError(ErrorCode.CONFIGURATION, "model timeout bound is invalid")
        provider_name, model_name = self._provider_model(provider.kind, candidate.model)
        return LLM(
            model=model_name,
            provider=provider_name,
            base_url=str(provider.endpoint).rstrip("/"),
            api_key=credential,
            temperature=self._config.crewai.temperature,
            timeout=min(
                self._config.crewai.model_timeout_seconds,
                timeout_seconds or self._config.crewai.model_timeout_seconds,
            ),
            max_tokens=self._config.crewai.model_max_output_tokens,
            # CrewAI's public LLM factory signature omits this field, while the
            # selected OpenAI-compatible provider exposes and honors it.
            max_retries=self._config.crewai.model_transport_retries,  # type: ignore[call-arg]
        )

    @staticmethod
    def _provider_model(kind: str, model: str) -> tuple[str, str]:
        mapping = {
            "ollama": ("ollama", model),
            "openai-compatible": ("openai", model),
            "anthropic": ("anthropic", model),
            "bedrock": ("bedrock", model),
        }
        try:
            return mapping[kind]
        except KeyError as exc:
            raise MishkanError(
                ErrorCode.CONFIGURATION,
                "provider kind is not supported by the CrewAI boundary",
                details={"kind": kind},
            ) from exc
