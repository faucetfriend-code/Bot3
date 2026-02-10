#!/usr/bin/env python3
"""
API Key Rotation System for Trading Bot.
Provides automated key lifecycle management, graceful transitions, and security monitoring.

Features:
- Scheduled key rotation with configurable intervals
- Graceful key transitions without service interruption
- Key expiration monitoring and alerts
- Integration with audit logging and monitoring systems
- Backup and recovery of rotated keys
"""

import os
import json
import asyncio
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, asdict
from pathlib import Path
import logging
from loguru import logger

from audit import audit_security_event, AuditEventType, AuditSeverity
from monitoring import AlertManager, AlertLevel
from encryption import EncryptionManager


@dataclass
class APIKey:
    """API key with metadata for rotation management."""

    key_id: str
    name: str  # Human-readable name (e.g., "Pacifica Mainnet")
    provider: str  # Exchange/provider name (e.g., "pacifica", "binance")
    environment: str  # "mainnet", "testnet", "staging"
    key_type: str  # "api_key", "secret_key", "access_token"

    encrypted_value: str
    created_at: datetime
    expires_at: Optional[datetime]
    last_rotated: Optional[datetime]
    rotation_interval_days: int
    is_active: bool = True
    metadata: Dict[str, Any] = None

    def is_expired(self) -> bool:
        """Check if the key has expired."""
        return self.expires_at and datetime.now() > self.expires_at

    def needs_rotation(self) -> bool:
        """Check if the key needs rotation based on schedule."""
        if not self.last_rotated:
            return True

        days_since_rotation = (datetime.now() - self.last_rotated).days
        return days_since_rotation >= self.rotation_interval_days

    def days_until_expiry(self) -> Optional[int]:
        """Get days until key expires."""
        if not self.expires_at:
            return None
        return max(0, (self.expires_at - datetime.now()).days)


@dataclass
class KeyRotationConfig:
    """Configuration for key rotation system."""

    enabled: bool = True
    rotation_check_interval_hours: int = 24  # Check for rotation daily
    default_rotation_interval_days: int = 90  # Rotate keys every 90 days
    expiry_warning_days: int = 7  # Warn 7 days before expiry
    max_concurrent_rotations: int = 3  # Max simultaneous rotations
    backup_keys_before_rotation: bool = True
    auto_disable_expired_keys: bool = True


class KeyRotationManager:
    """
    Manages API key rotation with automated scheduling and graceful transitions.
    """

    def __init__(self, config: KeyRotationConfig = None, encryption_key: str = None):
        self.config = config or KeyRotationConfig()
        self.encryption = EncryptionManager(encryption_key)
        self.keys_file = Path("./data/api_keys.json")
        self.backup_dir = Path("./data/key_backups")
        self.backup_dir.mkdir(parents=True, exist_ok=True)

        # In-memory key storage
        self.keys: Dict[str, APIKey] = {}
        self._lock = threading.Lock()

        # Alert manager for key-related alerts
        self.alert_manager = AlertManager()

        # Load existing keys
        self._load_keys()

        # Start background rotation checker
        if self.config.enabled:
            self._start_rotation_scheduler()

    def _load_keys(self):
        """Load API keys from encrypted storage."""
        try:
            if self.keys_file.exists():
                with open(self.keys_file, 'r') as f:
                    encrypted_data = f.read()

                # Decrypt the key data
                decrypted_data = self.encryption.decrypt(encrypted_data)
                keys_data = json.loads(decrypted_data)

                # Reconstruct APIKey objects
                for key_data in keys_data:
                    key_data['created_at'] = datetime.fromisoformat(key_data['created_at'])
                    if key_data.get('expires_at'):
                        key_data['expires_at'] = datetime.fromisoformat(key_data['expires_at'])
                    if key_data.get('last_rotated'):
                        key_data['last_rotated'] = datetime.fromisoformat(key_data['last_rotated'])
                    if not key_data.get('metadata'):
                        key_data['metadata'] = {}

                    key = APIKey(**key_data)
                    self.keys[key.key_id] = key

                logger.info(f"Loaded {len(self.keys)} API keys from storage")

        except Exception as e:
            logger.error(f"Failed to load API keys: {e}")
            # Initialize empty if loading fails
            self.keys = {}

    def _save_keys(self):
        """Save API keys to encrypted storage."""
        try:
            keys_data = []
            for key in self.keys.values():
                key_dict = asdict(key)
                # Convert datetime objects to ISO strings
                key_dict['created_at'] = key.created_at.isoformat()
                if key.expires_at:
                    key_dict['expires_at'] = key.expires_at.isoformat()
                if key.last_rotated:
                    key_dict['last_rotated'] = key.last_rotated.isoformat()

                keys_data.append(key_dict)

            # Encrypt the data
            json_data = json.dumps(keys_data, indent=2)
            encrypted_data = self.encryption.encrypt(json_data)

            # Write to file
            with open(self.keys_file, 'w') as f:
                f.write(encrypted_data)

            logger.info(f"Saved {len(self.keys)} API keys to storage")

        except Exception as e:
            logger.error(f"Failed to save API keys: {e}")
            raise

    def add_key(
        self,
        name: str,
        provider: str,
        environment: str,
        key_type: str,
        value: str,
        rotation_interval_days: Optional[int] = None,
        expires_at: Optional[datetime] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Add a new API key to the rotation system.

        Returns the key ID.
        """
        import uuid
        key_id = str(uuid.uuid4())

        key = APIKey(
            key_id=key_id,
            name=name,
            provider=provider,
            environment=environment,
            key_type=key_type,
            encrypted_value=self.encryption.encrypt(value),
            created_at=datetime.now(),
            expires_at=expires_at,
            last_rotated=None,
            rotation_interval_days=rotation_interval_days or self.config.default_rotation_interval_days,
            is_active=True,
            metadata=metadata or {}
        )

        with self._lock:
            self.keys[key_id] = key
            self._save_keys()

        # Audit: Key added
        audit_security_event(
            "security.key_added",
            f"key:{key_id}",
            {
                "key_name": name,
                "provider": provider,
                "environment": environment,
                "key_type": key_type,
                "rotation_interval_days": key.rotation_interval_days
            }
        )

        logger.info(f"Added new API key: {name} ({key_id})")
        return key_id

    def get_key(self, key_id: str) -> Optional[APIKey]:
        """Get an API key by ID."""
        return self.keys.get(key_id)

    def get_active_key(self, provider: str, environment: str, key_type: str) -> Optional[APIKey]:
        """Get the active key for a specific provider/environment/type combination."""
        for key in self.keys.values():
            if (key.provider == provider and
                key.environment == environment and
                key.key_type == key_type and
                key.is_active and
                not key.is_expired()):
                return key
        return None

    def get_key_value(self, key_id: str) -> Optional[str]:
        """Get the decrypted value of an API key."""
        key = self.get_key(key_id)
        if key:
            try:
                return self.encryption.decrypt(key.encrypted_value)
            except Exception as e:
                logger.error(f"Failed to decrypt key {key_id}: {e}")
                return None
        return None

    def rotate_key(self, key_id: str, new_value: str, reason: str = "scheduled_rotation") -> bool:
        """
        Rotate an API key with the new value.

        This creates a backup of the old key and updates to the new one.
        """
        key = self.get_key(key_id)
        if not key:
            logger.error(f"Key {key_id} not found for rotation")
            return False

        try:
            # Backup the old key
            if self.config.backup_keys_before_rotation:
                self._backup_key(key, f"pre_rotation_{reason}")

            # Update the key
            old_value = self.encryption.decrypt(key.encrypted_value)
            key.encrypted_value = self.encryption.encrypt(new_value)
            key.last_rotated = datetime.now()

            # Set new expiry if applicable
            if key.rotation_interval_days:
                key.expires_at = key.last_rotated + timedelta(days=key.rotation_interval_days)

            with self._lock:
                self._save_keys()

            # Audit: Key rotated
            audit_security_event(
                "security.key_rotated",
                f"key:{key_id}",
                {
                    "key_name": key.name,
                    "provider": key.provider,
                    "reason": reason,
                    "old_expiry": key.expires_at.isoformat() if key.expires_at else None,
                    "new_expiry": (key.last_rotated + timedelta(days=key.rotation_interval_days)).isoformat()
                }
            )

            logger.info(f"Successfully rotated key: {key.name} ({key_id}) - {reason}")

            # Alert about successful rotation
            task = asyncio.create_task(self._alert_key_rotated(key, reason))
            task.add_done_callback(
                lambda t: t.exception() and logger.error(f"Key rotation alert task failed: {t.exception()}")
            )

            return True

        except Exception as e:
            logger.error(f"Failed to rotate key {key_id}: {e}")

            # Audit: Rotation failed
            audit_security_event(
                "security.key_rotation_failed",
                f"key:{key_id}",
                {
                    "key_name": key.name,
                    "provider": key.provider,
                    "reason": reason,
                    "error": str(e)
                }
            )

            return False

    def disable_key(self, key_id: str, reason: str = "manual_disable") -> bool:
        """Disable an API key."""
        key = self.get_key(key_id)
        if not key:
            return False

        key.is_active = False

        with self._lock:
            self._save_keys()

        # Audit: Key disabled
        audit_security_event(
            "security.key_disabled",
            f"key:{key_id}",
            {
                "key_name": key.name,
                "provider": key.provider,
                "reason": reason
            }
        )

        logger.info(f"Disabled API key: {key.name} ({key_id}) - {reason}")
        return True

    def delete_key(self, key_id: str, reason: str = "manual_delete") -> bool:
        """Delete an API key permanently."""
        key = self.get_key(key_id)
        if not key:
            return False

        # Backup before deletion
        self._backup_key(key, f"pre_deletion_{reason}")

        with self._lock:
            del self.keys[key_id]
            self._save_keys()

        # Audit: Key deleted
        audit_security_event(
            "security.key_deleted",
            f"key:{key_id}",
            {
                "key_name": key.name,
                "provider": key.provider,
                "reason": reason
            }
        )

        logger.info(f"Deleted API key: {key.name} ({key_id}) - {reason}")
        return True

    def _backup_key(self, key: APIKey, reason: str):
        """Backup a key before modification."""
        try:
            backup_data = {
                "backup_timestamp": datetime.now().isoformat(),
                "reason": reason,
                "key_data": asdict(key)
            }

            backup_file = self.backup_dir / f"key_backup_{key.key_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            with open(backup_file, 'w') as f:
                json.dump(backup_data, f, indent=2, default=str)

            logger.debug(f"Backed up key {key.key_id} to {backup_file}")

        except Exception as e:
            logger.error(f"Failed to backup key {key.key_id}: {e}")

    async def _alert_key_rotated(self, key: APIKey, reason: str):
        """Send alert about successful key rotation."""
        try:
            await self.alert_manager.send_alert(
                level=AlertLevel.INFO,
                title=f"API Key Rotated: {key.name}",
                message=f"Successfully rotated {key.key_type} for {key.provider} ({key.environment})",
                source="key_rotation_system",
                metadata={
                    "key_id": key.key_id,
                    "key_name": key.name,
                    "provider": key.provider,
                    "environment": key.environment,
                    "reason": reason,
                    "next_rotation": (key.last_rotated + timedelta(days=key.rotation_interval_days)).isoformat() if key.last_rotated else None
                }
            )
        except Exception as e:
            logger.error(f"Failed to send key rotation alert: {e}")

    def check_key_expirations(self) -> List[Dict[str, Any]]:
        """Check for keys that are expiring soon and return alerts."""
        alerts = []

        for key in self.keys.values():
            if not key.is_active:
                continue

            days_until_expiry = key.days_until_expiry()
            if days_until_expiry is None:
                continue

            if days_until_expiry <= self.config.expiry_warning_days:
                alert_level = AlertLevel.CRITICAL if days_until_expiry <= 1 else AlertLevel.WARNING

                alerts.append({
                    "key_id": key.key_id,
                    "key_name": key.name,
                    "provider": key.provider,
                    "environment": key.environment,
                    "days_until_expiry": days_until_expiry,
                    "expires_at": key.expires_at.isoformat(),
                    "alert_level": alert_level.value
                })

                # Send alert
                task = asyncio.create_task(self._alert_key_expiring(key, days_until_expiry, alert_level))
                task.add_done_callback(
                    lambda t: t.exception() and logger.error(f"Key expiry alert task failed: {t.exception()}")
                )

        return alerts

    async def _alert_key_expiring(self, key: APIKey, days_left: int, level: AlertLevel):
        """Send alert about expiring key."""
        try:
            await self.alert_manager.send_alert(
                level=level,
                title=f"API Key Expiring Soon: {key.name}",
                message=f"{key.key_type} for {key.provider} ({key.environment}) expires in {days_left} days",
                source="key_rotation_system",
                metadata={
                    "key_id": key.key_id,
                    "key_name": key.name,
                    "provider": key.provider,
                    "environment": key.environment,
                    "expires_at": key.expires_at.isoformat(),
                    "days_left": days_left
                }
            )
        except Exception as e:
            logger.error(f"Failed to send key expiry alert: {e}")

    def get_keys_needing_rotation(self) -> List[APIKey]:
        """Get list of keys that need rotation."""
        return [key for key in self.keys.values() if key.is_active and key.needs_rotation()]

    def get_expired_keys(self) -> List[APIKey]:
        """Get list of expired keys."""
        return [key for key in self.keys.values() if key.is_active and key.is_expired()]

    async def perform_scheduled_rotations(self):
        """Perform scheduled key rotations."""
        try:
            keys_to_rotate = self.get_keys_needing_rotation()
            expired_keys = self.get_expired_keys()

            logger.info(f"Found {len(keys_to_rotate)} keys needing rotation, {len(expired_keys)} expired keys")

            # Handle expired keys
            for key in expired_keys:
                if self.config.auto_disable_expired_keys:
                    logger.warning(f"Auto-disabling expired key: {key.name} ({key.key_id})")
                    self.disable_key(key.key_id, "auto_expired")

                    # Audit: Key auto-disabled due to expiry
                    audit_security_event(
                        "security.key_auto_disabled",
                        f"key:{key.key_id}",
                        {
                            "key_name": key.name,
                            "provider": key.provider,
                            "expired_at": key.expires_at.isoformat()
                        }
                    )

            # Perform rotations (limited concurrency)
            semaphore = asyncio.Semaphore(self.config.max_concurrent_rotations)

            async def rotate_with_limit(key: APIKey):
                async with semaphore:
                    await self._perform_key_rotation(key)

            # Rotate keys that need it
            rotation_tasks = [rotate_with_limit(key) for key in keys_to_rotate[:self.config.max_concurrent_rotations]]
            if rotation_tasks:
                await asyncio.gather(*rotation_tasks, return_exceptions=True)

        except Exception as e:
            logger.error(f"Error during scheduled rotations: {e}")

    async def _perform_key_rotation(self, key: APIKey):
        """Perform rotation for a specific key."""
        try:
            # This is a placeholder - in real implementation, this would:
            # 1. Generate new API key from the provider
            # 2. Test the new key
            # 3. Rotate to the new key
            # 4. Clean up old key

            logger.info(f"Performing rotation for key: {key.name} ({key.key_id})")

            # For now, just mark as rotated (simulating successful rotation)
            # In production, this would integrate with each provider's API

            # Simulate key rotation delay
            await asyncio.sleep(0.1)

            # For Pacifica keys, we would need to:
            # - Call their API to generate new keys
            # - Update the configuration
            # - Test connectivity
            # - Rotate the key

            logger.warning(f"Key rotation simulation completed for: {key.name} (manual rotation required)")

        except Exception as e:
            logger.error(f"Failed to rotate key {key.key_id}: {e}")

    def _start_rotation_scheduler(self):
        """Start the background rotation scheduler."""
        def rotation_worker():
            while True:
                try:
                    # Check for expirations and send alerts
                    self.check_key_expirations()

                    # Run scheduled rotations
                    asyncio.run(self.perform_scheduled_rotations())

                except Exception as e:
                    logger.error(f"Error in rotation scheduler: {e}")

                # Sleep until next check
                import time
                time.sleep(self.config.rotation_check_interval_hours * 3600)

        thread = threading.Thread(target=rotation_worker, daemon=True)
        thread.start()
        logger.info("Started API key rotation scheduler")

    def get_key_status_report(self) -> Dict[str, Any]:
        """Generate a comprehensive status report for all keys."""
        total_keys = len(self.keys)
        active_keys = len([k for k in self.keys.values() if k.is_active])
        expired_keys = len(self.get_expired_keys())
        keys_needing_rotation = len(self.get_keys_needing_rotation())

        keys_by_provider = {}
        for key in self.keys.values():
            if key.provider not in keys_by_provider:
                keys_by_provider[key.provider] = []
            keys_by_provider[key.provider].append({
                "key_id": key.key_id,
                "name": key.name,
                "environment": key.environment,
                "is_active": key.is_active,
                "is_expired": key.is_expired(),
                "needs_rotation": key.needs_rotation(),
                "days_until_expiry": key.days_until_expiry(),
                "last_rotated": key.last_rotated.isoformat() if key.last_rotated else None
            })

        return {
            "summary": {
                "total_keys": total_keys,
                "active_keys": active_keys,
                "expired_keys": expired_keys,
                "keys_needing_rotation": keys_needing_rotation
            },
            "keys_by_provider": keys_by_provider,
            "generated_at": datetime.now().isoformat()
        }


# Global key rotation manager instance
key_rotation_manager = KeyRotationManager()


# Convenience functions for common operations
def add_pacifica_key(name: str, api_key: str, secret_key: str, environment: str = "mainnet") -> tuple[str, str]:
    """Add Pacifica API key pair."""
    api_key_id = key_rotation_manager.add_key(
        name=f"{name} API Key",
        provider="pacifica",
        environment=environment,
        key_type="api_key",
        value=api_key,
        rotation_interval_days=90
    )

    secret_key_id = key_rotation_manager.add_key(
        name=f"{name} Secret Key",
        provider="pacifica",
        environment=environment,
        key_type="secret_key",
        value=secret_key,
        rotation_interval_days=90
    )

    return api_key_id, secret_key_id


def get_pacifica_keys(environment: str = "mainnet") -> tuple[Optional[str], Optional[str]]:
    """Get active Pacifica API key pair."""
    api_key = key_rotation_manager.get_active_key("pacifica", environment, "api_key")
    secret_key = key_rotation_manager.get_active_key("pacifica", environment, "secret_key")

    api_value = key_rotation_manager.get_key_value(api_key.key_id) if api_key else None
    secret_value = key_rotation_manager.get_key_value(secret_key.key_id) if secret_key else None

    return api_value, secret_value


def rotate_pacifica_keys(environment: str = "mainnet", new_api_key: str = None, new_secret_key: str = None) -> bool:
    """Rotate Pacifica API keys."""
    # This would need to be implemented with actual Pacifica API integration
    # For now, it's a placeholder
    logger.warning("Pacifica key rotation requires manual API integration")
    return False