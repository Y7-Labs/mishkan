"""Persisted schema compatibility without implicit migration."""

from collections.abc import Mapping

from mishkan.domain.errors import ErrorCode, MishkanError


class SchemaRegistry:
    """Closed registry of versions understood by the running release."""

    _supported: Mapping[str, frozenset[str]] = {
        "mishkan.config": frozenset({"1.0", "1.1", "1.2", "1.3", "1.4", "1.5", "1.6"}),
        "mishkan.context-pack": frozenset({"1.0"}),
        "mishkan.error": frozenset({"1.0"}),
        "mishkan.discovery": frozenset({"1.0"}),
        "mishkan.environment-profile": frozenset({"1.0"}),
        "mishkan.inspection": frozenset({"1.0"}),
        "mishkan.isolation": frozenset({"1.0"}),
        "mishkan.knowledge": frozenset({"1.0"}),
        "mishkan.organization": frozenset({"1.0"}),
        "mishkan.organization-roster": frozenset({"1.0"}),
        "mishkan.outcome": frozenset({"1.0"}),
        "mishkan.plan": frozenset({"1.0", "1.1", "1.2"}),
        "mishkan.policy": frozenset({"1.0"}),
        "mishkan.record": frozenset({"1.0"}),
        "mishkan.skill": frozenset({"1.0"}),
        "mishkan.tool": frozenset({"1.0"}),
        "mishkan.tool-source": frozenset({"1.0"}),
    }

    @classmethod
    def supported_versions(cls, contract: str) -> frozenset[str]:
        return cls._supported.get(contract, frozenset())

    @classmethod
    def require_supported(cls, contract: str, version: object) -> str:
        normalized = str(version) if version is not None else ""
        supported = cls.supported_versions(contract)
        if normalized not in supported:
            raise MishkanError(
                ErrorCode.VERSION,
                f"unsupported schema version for {contract}",
                details={
                    "contract": contract,
                    "received": normalized or None,
                    "supported": sorted(supported),
                    "automatic_migration": False,
                },
            )
        return normalized
