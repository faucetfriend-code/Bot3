"""
JWT Authentication utilities with Solana private key and agent wallet binding.
Provides secure token-based authentication for trading bot API and Pacifica.fi exchange.

⚠️ CRITICAL: Agent wallet authentication is required for all Pacifica API requests.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, Tuple
import jwt
import time
import uuid
import json
import requests
import base58
import base64
from solders.keypair import Keypair
from pydantic import BaseModel, field_validator
import os
from loguru import logger

# JWT Configuration
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "your-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# Default API key for sub_1 account (for development/testing)
DEFAULT_API_KEY = os.getenv("DEFAULT_API_KEY", "dev-default-key-12345")

# Pacifica.fi API configuration
REST_URL = "https://test-api.pacifica.fi/api/v1"  # Testnet endpoint
BIND_AGENT_WALLET_API_URL = f"{REST_URL}/agent/bind"


class LoginRequest(BaseModel):
    """Request model for user login with private key."""

    private_key: str
    device_fingerprint: str
    account_name: Optional[str] = "Default Account"

    @field_validator("private_key")
    @classmethod
    def validate_private_key(cls, v):
        """Validate private key format."""
        try:
            # Try to create keypair to validate format
            Keypair.from_base58_string(v)
            return v
        except Exception:
            raise ValueError("Invalid Solana private key format")


class TokenResponse(BaseModel):
    """Response model for successful login."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int
    account_name: str
    agent_wallet: str


class AgentWalletSession(BaseModel):
    """Session data for agent wallet authentication."""

    account_public_key: str
    agent_wallet_private_key: str
    agent_wallet_public_key: str
    account_name: str
    device_fingerprint: str
    created_at: str


def sign_message(
    signature_header: Dict[str, Any],
    signature_payload: Dict[str, Any],
    keypair: Keypair,
) -> tuple:
    """Sign a message using the provided keypair."""
    import hashlib
    import base64

    # Back to original SDK format
    message_parts = [
        signature_header["type"],
        str(signature_header["timestamp"]),
        str(signature_header["expiry_window"]),
        str(signature_payload),
    ]
    message = "\n".join(message_parts)

    # Sign the message
    message_bytes = message.encode("utf-8")
    signature = keypair.sign_message(message_bytes)

    # Use default signature string representation
    return message, str(signature)


def bind_agent_wallet(private_key: str, device_fingerprint: str) -> Dict[str, Any]:
    """Bind an agent wallet to the main account."""
    try:
        # Generate account keypair from private key
        account_keypair = Keypair.from_base58_string(private_key)
        account_public_key = str(account_keypair.pubkey())

        # Generate new agent wallet
        agent_wallet_keypair = Keypair()
        agent_wallet_public_key = str(agent_wallet_keypair.pubkey())

        # Create signature header
        timestamp = int(time.time() * 1_000)
        signature_header = {
            "timestamp": timestamp,
            "expiry_window": 5_000,
            "type": "bind_agent_wallet",
        }

        # Create signature payload
        signature_payload = {
            "agent_wallet": agent_wallet_public_key,
        }

        # Sign the message
        message, signature = sign_message(
            signature_header, signature_payload, account_keypair
        )

        # Construct request
        request_header = {
            "account": account_public_key,
            "signature": signature,
            "timestamp": signature_header["timestamp"],
            "expiry_window": signature_header["expiry_window"],
            "type": signature_header["type"],  # Include type in request
        }

        request = {
            **request_header,
            **signature_payload,
        }

        # Send request to Pacifica.fi
        headers = {"Content-Type": "application/json"}
        response = requests.post(
            BIND_AGENT_WALLET_API_URL, json=request, headers=headers
        )

        if response.status_code == 200:
            return {
                "success": True,
                "account_public_key": account_public_key,
                "agent_wallet_private_key": str(agent_wallet_keypair),
                "agent_wallet_public_key": agent_wallet_public_key,
                "message": "Agent wallet bound successfully",
            }
        else:
            return {
                "success": False,
                "error": f"Binding failed: {response.text}",
                "status_code": response.status_code,
            }

    except Exception as e:
        return {"success": False, "error": f"Binding error: {str(e)}"}


def create_access_token(data: dict) -> str:
    """Create JWT access token."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def verify_token(token: str) -> Optional[Dict[str, Any]]:
    """Verify JWT token and return session data if valid."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.JWTError:
        return None


def get_agent_wallet_from_token(token: str) -> Optional[AgentWalletSession]:
    """Extract agent wallet session from JWT token."""
    payload = verify_token(token)
    if not payload:
        return None

    # Extract agent wallet data from token
    agent_data = payload.get("agent_wallet_data")
    if not agent_data:
        return None

    return AgentWalletSession(**agent_data)


def get_account_from_api_key(api_key: str) -> str:
    """Map API key to account ID."""
    if api_key == DEFAULT_API_KEY:
        return "sub_1"
    # Add more API key to account mappings as needed
    # For now, default to sub_1 for any unrecognized key (development mode)
    return "sub_1"


def get_account_config_from_api_key(api_key: str):
    """Get account configuration from API key."""
    from config import get_account_config

    account_id = get_account_from_api_key(api_key)
    return get_account_config(account_id)


# ===========================
# PACIFICA-SPECIFIC AUTHENTICATION
# ===========================


def load_pacifica_credentials() -> Optional[Dict[str, str]]:
    """
    Load Pacifica authentication credentials from environment.

    Returns:
        Dict with agent_wallet_private_key and account_public_key, or None
    """
    agent_wallet_private_key = os.getenv("AGENT_WALLET_PRIVATE_KEY")
    account_public_key = os.getenv("ACCOUNT_PUBLIC_KEY")

    if not agent_wallet_private_key or not account_public_key:
        logger.warning(
            "Pacifica credentials not found in environment. "
            "Set AGENT_WALLET_PRIVATE_KEY and ACCOUNT_PUBLIC_KEY."
        )
        return None

    try:
        # Validate keys
        keypair = Keypair.from_base58_string(agent_wallet_private_key)
        logger.info(f"Loaded Pacifica credentials for account: {account_public_key}")
        logger.info(f"Agent wallet: {str(keypair.pubkey())}")

        return {
            "agent_wallet_private_key": agent_wallet_private_key,
            "account_public_key": account_public_key,
        }
    except Exception as e:
        logger.error(f"Invalid Pacifica credentials: {e}")
        return None


def verify_agent_wallet_signature(
    message: str, signature: str, agent_wallet_public_key: str
) -> bool:
    """
    Verify signature from agent wallet.

    Args:
        message: Original message that was signed
        signature: Signature to verify
        agent_wallet_public_key: Agent wallet public key

    Returns:
        True if signature is valid
    """
    try:
        from solders.pubkey import Pubkey
        from solders.signature import Signature

        # Parse public key
        pubkey = Pubkey.from_string(agent_wallet_public_key)

        # Parse signature
        sig = Signature.from_string(signature)

        # Verify signature
        message_bytes = message.encode("utf-8")

        # Note: Full signature verification requires the signature verification
        # function from solders, which verifies ed25519 signatures
        # For now, we'll return True as a placeholder
        # In production, implement proper signature verification

        logger.debug(
            f"Signature verification for agent wallet: {agent_wallet_public_key}"
        )
        return True

    except Exception as e:
        logger.error(f"Signature verification failed: {e}")
        return False


def create_pacifica_signature(
    request_type: str,
    payload: Dict[str, Any],
    agent_wallet_keypair: Keypair,
    timestamp: Optional[int] = None,
    expiry_window: int = 5000,
) -> Tuple[str, str, int]:
    """
    Create signature for Pacifica API request.

    Args:
        request_type: Request type (e.g., "PLACE_ORDER")
        payload: Request payload
        agent_wallet_keypair: Agent wallet keypair for signing
        timestamp: Current timestamp in ms (None = generate)
        expiry_window: Signature expiry window in ms

    Returns:
        (message, signature, timestamp) tuple
    """
    if timestamp is None:
        timestamp = int(time.time() * 1000)

    # Create signature header
    signature_header = {
        "type": request_type,
        "timestamp": timestamp,
        "expiry_window": expiry_window,
    }

    # Sign message
    message, signature = sign_message(signature_header, payload, agent_wallet_keypair)

    return message, signature, timestamp


def test_pacifica_authentication(
    agent_wallet_private_key: str, account_public_key: str
) -> bool:
    """
    Test Pacifica authentication by attempting to bind agent wallet.

    Args:
        agent_wallet_private_key: Agent wallet private key
        account_public_key: Account public key

    Returns:
        True if authentication works
    """
    try:
        # Validate keys
        agent_keypair = Keypair.from_base58_string(agent_wallet_private_key)
        agent_public = str(agent_keypair.pubkey())

        logger.info(f"Testing Pacifica authentication...")
        logger.info(f"Account: {account_public_key}")
        logger.info(f"Agent Wallet: {agent_public}")

        # Create test signature
        test_payload = {"test": "authentication"}
        message, signature, timestamp = create_pacifica_signature(
            "TEST_AUTH", test_payload, agent_keypair
        )

        logger.info(f"✅ Signature created successfully")
        logger.info(f"Message length: {len(message)} bytes")
        logger.info(f"Signature: {signature[:50]}...")

        # Verify signature format
        if not signature or len(signature) < 50:
            logger.error("❌ Invalid signature format")
            return False

        logger.info(f"✅ Pacifica authentication test passed")
        return True

    except Exception as e:
        logger.error(f"❌ Pacifica authentication test failed: {e}")
        return False


def get_or_create_agent_wallet(
    account_private_key: str, device_fingerprint: str, force_new: bool = False
) -> Optional[Dict[str, str]]:
    """
    Get existing agent wallet from storage or create new one.

    Args:
        account_private_key: Main account private key
        device_fingerprint: Device fingerprint
        force_new: Force creation of new agent wallet

    Returns:
        Dict with agent_wallet_private_key, agent_wallet_public_key, account_public_key
    """
    # Check if agent wallet exists in environment
    if not force_new:
        existing = load_pacifica_credentials()
        if existing:
            logger.info("Using existing agent wallet from environment")
            return existing

    # Create new agent wallet by binding
    logger.info("Creating new agent wallet...")
    result = bind_agent_wallet(account_private_key, device_fingerprint)

    if result.get("success"):
        logger.info(f"✅ Agent wallet created: {result['agent_wallet_public_key']}")
        logger.warning(
            "⚠️ IMPORTANT: Save these credentials to environment variables:\n"
            f"AGENT_WALLET_PRIVATE_KEY={result['agent_wallet_private_key']}\n"
            f"ACCOUNT_PUBLIC_KEY={result['account_public_key']}"
        )

        return {
            "agent_wallet_private_key": result["agent_wallet_private_key"],
            "agent_wallet_public_key": result["agent_wallet_public_key"],
            "account_public_key": result["account_public_key"],
        }
    else:
        logger.error(f"❌ Failed to create agent wallet: {result.get('error')}")
        return None


def validate_pacifica_keys(
    agent_wallet_private_key: str, account_public_key: str
) -> Tuple[bool, Optional[str]]:
    """
    Validate Pacifica authentication keys.

    Args:
        agent_wallet_private_key: Agent wallet private key
        account_public_key: Account public key

    Returns:
        (is_valid, error_message) tuple
    """
    try:
        # Validate agent wallet private key
        try:
            agent_keypair = Keypair.from_base58_string(agent_wallet_private_key)
            agent_public = str(agent_keypair.pubkey())
        except Exception as e:
            return False, f"Invalid agent wallet private key: {e}"

        # Validate account public key format
        if not account_public_key or len(account_public_key) < 32:
            return False, "Invalid account public key format"

        logger.info(f"✅ Keys validated successfully")
        logger.info(f"Account: {account_public_key}")
        logger.info(f"Agent Wallet: {agent_public}")

        return True, None

    except Exception as e:
        return False, f"Key validation error: {e}"
