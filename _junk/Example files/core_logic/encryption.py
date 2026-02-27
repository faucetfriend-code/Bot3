"""
Encryption utilities for securely storing sensitive data.
Uses Fernet symmetric encryption from the cryptography library.
"""

import os
import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from typing import Optional
from loguru import logger


class EncryptionManager:
    """Manages encryption/decryption of sensitive data using Fernet."""

    def __init__(self, master_key: Optional[str] = None):
        """
        Initialize encryption manager.

        Args:
            master_key: Master encryption key from environment. If None, will load from env.
        """
        if master_key is None:
            master_key = os.getenv("MASTER_ENCRYPTION_KEY")

        if not master_key:
            # Generate a random key for development (WARNING: not persistent!)
            logger.warning(
                "MASTER_ENCRYPTION_KEY not set in .env! "
                "Generating temporary key - encrypted data will NOT persist across restarts!"
            )
            master_key = Fernet.generate_key().decode()

        # Derive a Fernet key from the master key
        self.fernet = self._create_fernet(master_key)

    def _create_fernet(self, master_key: str) -> Fernet:
        """Create a Fernet cipher from the master key."""
        # Use PBKDF2 to derive a proper 32-byte key
        salt = os.getenv("ENCRYPTION_SALT", "trading-bot-salt-2025").encode()

        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )

        key = base64.urlsafe_b64encode(kdf.derive(master_key.encode()))
        return Fernet(key)

    def encrypt(self, plaintext: str) -> str:
        """
        Encrypt a string.

        Args:
            plaintext: The string to encrypt

        Returns:
            Base64-encoded encrypted string
        """
        try:
            encrypted_bytes = self.fernet.encrypt(plaintext.encode())
            return encrypted_bytes.decode()
        except Exception as e:
            logger.error(f"Encryption failed: {e}")
            raise

    def decrypt(self, ciphertext: str) -> str:
        """
        Decrypt an encrypted string.

        Args:
            ciphertext: Base64-encoded encrypted string

        Returns:
            Decrypted plaintext string
        """
        try:
            decrypted_bytes = self.fernet.decrypt(ciphertext.encode())
            return decrypted_bytes.decode()
        except Exception as e:
            logger.error(f"Decryption failed: {e}")
            raise ValueError("Failed to decrypt data. Key may be incorrect.")


# Global instance
_encryption_manager: Optional[EncryptionManager] = None


def get_encryption_manager() -> EncryptionManager:
    """Get or create global encryption manager instance."""
    global _encryption_manager
    if _encryption_manager is None:
        _encryption_manager = EncryptionManager()
    return _encryption_manager


def encrypt_private_key(private_key: str) -> str:
    """Encrypt a private key for storage."""
    return get_encryption_manager().encrypt(private_key)


def decrypt_private_key(encrypted_key: str) -> str:
    """Decrypt a stored private key."""
    return get_encryption_manager().decrypt(encrypted_key)
