"""
Feature Flags for Gradual Rollout of New Architecture

Controls enablement of Phase 1 and Phase 2 features during transition.
Allows safe deployment and rollback capabilities.
"""

import os
from typing import Dict, Any
from loguru import logger


class FeatureFlags:
    """Centralized feature flag management for architecture transitions."""

    def __init__(self):
        # Phase 1 Features
        self.enable_grid_lifecycle_manager = self._get_bool_env(
            "ENABLE_GRID_LIFECYCLE_MANAGER", True
        )
        self.enable_market_regime_detector = self._get_bool_env(
            "ENABLE_MARKET_REGIME_DETECTOR", True
        )
        self.enable_authoritative_risk_manager = self._get_bool_env(
            "ENABLE_AUTHORITATIVE_RISK_MANAGER", True
        )

        # Phase 2 Features
        self.enable_component_registry = self._get_bool_env(
            "ENABLE_COMPONENT_REGISTRY", True
        )
        self.enable_event_system = self._get_bool_env("ENABLE_EVENT_SYSTEM", True)
        self.enable_coordinator_trading_bot = self._get_bool_env(
            "ENABLE_COORDINATOR_TRADING_BOT", True
        )
        self.enable_websocket_only_prices = self._get_bool_env(
            "ENABLE_WEBSOCKET_ONLY_PRICES", True
        )

        # Legacy Features (for rollback)
        self.enable_legacy_trading_bot = self._get_bool_env(
            "ENABLE_LEGACY_TRADING_BOT", False
        )
        self.enable_rest_api_fallback = self._get_bool_env(
            "ENABLE_REST_API_FALLBACK", False
        )

        logger.info("Feature flags initialized")
        self._log_feature_status()

    def _get_bool_env(self, key: str, default: bool = False) -> bool:
        """Get boolean value from environment variable."""
        value = os.getenv(key, str(default)).lower()
        return value in ("true", "1", "yes", "on")

    def _log_feature_status(self):
        """Log current feature flag status."""
        features = {
            "Phase 1 - Grid Lifecycle Manager": self.enable_grid_lifecycle_manager,
            "Phase 1 - Market Regime Detector": self.enable_market_regime_detector,
            "Phase 1 - Authoritative Risk Manager": self.enable_authoritative_risk_manager,
            "Phase 2 - Component Registry": self.enable_component_registry,
            "Phase 2 - Event System": self.enable_event_system,
            "Phase 2 - Coordinator Trading Bot": self.enable_coordinator_trading_bot,
            "Phase 2 - WebSocket Only Prices": self.enable_websocket_only_prices,
            "Legacy - Trading Bot": self.enable_legacy_trading_bot,
            "Legacy - REST API Fallback": self.enable_rest_api_fallback,
        }

        enabled = [k for k, v in features.items() if v]
        disabled = [k for k, v in features.items() if not v]

        logger.info(f"Enabled features ({len(enabled)}): {', '.join(enabled)}")
        logger.info(f"Disabled features ({len(disabled)}): {', '.join(disabled)}")

    def is_phase1_enabled(self) -> bool:
        """Check if all Phase 1 features are enabled."""
        return all(
            [
                self.enable_grid_lifecycle_manager,
                self.enable_market_regime_detector,
                self.enable_authoritative_risk_manager,
            ]
        )

    def is_phase2_enabled(self) -> bool:
        """Check if all Phase 2 features are enabled."""
        return all(
            [
                self.enable_component_registry,
                self.enable_event_system,
                self.enable_coordinator_trading_bot,
                self.enable_websocket_only_prices,
            ]
        )

    def is_legacy_mode(self) -> bool:
        """Check if legacy mode is enabled (rollback)."""
        return self.enable_legacy_trading_bot

    def get_feature_status(self) -> Dict[str, Any]:
        """Get comprehensive feature flag status."""
        return {
            "phase1_enabled": self.is_phase1_enabled(),
            "phase2_enabled": self.is_phase2_enabled(),
            "legacy_mode": self.is_legacy_mode(),
            "features": {
                "grid_lifecycle_manager": self.enable_grid_lifecycle_manager,
                "market_regime_detector": self.enable_market_regime_detector,
                "authoritative_risk_manager": self.enable_authoritative_risk_manager,
                "component_registry": self.enable_component_registry,
                "event_system": self.enable_event_system,
                "coordinator_trading_bot": self.enable_coordinator_trading_bot,
                "websocket_only_prices": self.enable_websocket_only_prices,
                "legacy_trading_bot": self.enable_legacy_trading_bot,
                "rest_api_fallback": self.enable_rest_api_fallback,
            },
        }

    def can_enable_feature(self, feature_name: str) -> bool:
        """
        Check if a feature can be safely enabled based on dependencies.

        Args:
            feature_name: Name of the feature to check

        Returns:
            True if feature can be enabled, False otherwise
        """
        dependencies = {
            "coordinator_trading_bot": ["component_registry", "event_system"],
            "websocket_only_prices": [],  # Independent
            "grid_lifecycle_manager": [],  # Independent
            "market_regime_detector": [],  # Independent
            "authoritative_risk_manager": [],  # Independent
        }

        required_deps = dependencies.get(feature_name, [])
        return all(getattr(self, f"enable_{dep}", False) for dep in required_deps)

    def enable_feature(self, feature_name: str) -> bool:
        """
        Enable a feature if dependencies are met.

        Args:
            feature_name: Name of the feature to enable

        Returns:
            True if enabled, False if dependencies not met
        """
        if self.can_enable_feature(feature_name):
            setattr(self, f"enable_{feature_name}", True)
            logger.info(f"Feature enabled: {feature_name}")
            return True
        else:
            logger.warning(
                f"Cannot enable feature {feature_name} - dependencies not met"
            )
            return False

    def disable_feature(self, feature_name: str) -> None:
        """
        Disable a feature (for rollback or maintenance).

        Args:
            feature_name: Name of the feature to disable
        """
        setattr(self, f"enable_{feature_name}", False)
        logger.info(f"Feature disabled: {feature_name}")

    def emergency_rollback(self) -> None:
        """Emergency rollback to legacy mode."""
        logger.critical("EMERGENCY ROLLBACK initiated")

        # Disable all new features
        self.enable_grid_lifecycle_manager = False
        self.enable_market_regime_detector = False
        self.enable_authoritative_risk_manager = False
        self.enable_component_registry = False
        self.enable_event_system = False
        self.enable_coordinator_trading_bot = False
        self.enable_websocket_only_prices = False

        # Enable legacy features
        self.enable_legacy_trading_bot = True
        self.enable_rest_api_fallback = True

        logger.critical("Emergency rollback complete - legacy mode activated")


# Global feature flags instance
_feature_flags = FeatureFlags()


def get_feature_flags() -> FeatureFlags:
    """Get the global feature flags instance."""
    return _feature_flags
