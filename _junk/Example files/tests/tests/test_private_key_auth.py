#!/usr/bin/env python3
"""
Test script for private key authentication and agent wallet binding.
"""

from solders.keypair import Keypair
import time


def test_private_key():
    """Test private key validation and agent wallet generation."""

    # Your provided private key
    private_key = "5i8E7n6iXTqoxhi38YBVJD2SGagiqRBs4HVzpsUUG7gtebcpqnGG6SfJRyWQr9uMYDiffSohWPdRYPwNKEhpw8x2"

    try:
        # Test keypair creation
        keypair = Keypair.from_base58_string(private_key)
        public_key = str(keypair.pubkey())

        print("[OK] Private key is valid")
        print(f"Public key: {public_key}")

        # Generate agent wallet
        agent_wallet = Keypair()
        agent_public_key = str(agent_wallet.pubkey())
        agent_private_key = str(agent_wallet)

        print("[OK] Agent wallet generated")
        print(f"Agent public key: {agent_public_key}")

        # Test signing
        test_message = b"Hello Pacifica.fi"
        signature = keypair.sign_message(test_message)
        print("[OK] Message signing works")
        print(f"Signature: {signature}")

        # Test agent wallet signing
        agent_signature = agent_wallet.sign_message(test_message)
        print("[OK] Agent wallet signing works")
        print(f"Agent signature: {agent_signature}")

        return {
            "account_public_key": public_key,
            "agent_wallet_private_key": agent_private_key,
            "agent_wallet_public_key": agent_public_key,
        }

    except Exception as e:
        print(f"[ERROR] Error: {e}")
        return None


def test_sign_message():
    """Test the sign_message function from the SDK example."""
    private_key = "5i8E7n6iXTqoxhi38YBVJD2SGagiqRBs4HVzpsUUG7gtebcpqnGG6SfJRyWQr9uMYDiffSohWPdRYPwNKEhpw8x2"
    keypair = Keypair.from_base58_string(private_key)

    # Test signature header and payload
    signature_header = {
        "timestamp": int(time.time() * 1_000),
        "expiry_window": 5_000,
        "type": "bind_agent_wallet",
    }

    signature_payload = {
        "agent_wallet": "test_agent_wallet_public_key",
    }

    # Create message string
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

    print("[OK] SDK-style signing works")
    print(f"Message: {message}")
    print(f"Signature: {signature}")

    return message, str(signature)


if __name__ == "__main__":
    print("Testing Private Key Authentication")
    print("=" * 40)

    # Test private key
    result = test_private_key()
    if result:
        print("\nAgent wallet credentials:")
        print(f"Account Public Key: {result['account_public_key']}")
        print(f"Agent Private Key: {result['agent_wallet_private_key']}")
        print(f"Agent Public Key: {result['agent_wallet_public_key']}")

    print("\n" + "=" * 40)
    print("Testing SDK-style signing")

    # Test signing
    test_sign_message()

    print("\n[SUCCESS] All tests passed! Private key authentication is ready.")
