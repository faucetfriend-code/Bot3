#!/usr/bin/env python3
"""
Simple test for the private key authentication functions.
"""

from auth import bind_agent_wallet, LoginRequest


def test_bind_agent_wallet():
    """Test the agent wallet binding function."""

    # Your private key
    private_key = "5i8E7n6iXTqoxhi38YBVJD2SGagiqRBs4HVzpsUUG7gtebcpqnGG6SfJRyWQr9uMYDiffSohWPdRYPwNKEhpw8x2"

    print("Testing agent wallet binding...")
    print(f"Private key: {private_key[:20]}...")

    try:
        result = bind_agent_wallet(private_key, "test_device_123")

        if result["success"]:
            print("[SUCCESS] Agent wallet binding successful!")
            print(f"Account Public Key: {result['account_public_key']}")
            print(f"Agent Wallet Public Key: {result['agent_wallet_public_key']}")
            print(
                f"Agent Wallet Private Key: {result['agent_wallet_private_key'][:20]}..."
            )
            return True
        else:
            print(f"[ERROR] Binding failed: {result['error']}")
            return False

    except Exception as e:
        print(f"[ERROR] Exception during binding: {e}")
        return False


def test_login_request_validation():
    """Test the LoginRequest validation."""

    print("\nTesting LoginRequest validation...")

    try:
        # Valid request
        request = LoginRequest(
            private_key="5i8E7n6iXTqoxhi38YBVJD2SGagiqRBs4HVzpsUUG7gtebcpqnGG6SfJRyWQr9uMYDiffSohWPdRYPwNKEhpw8x2",
            device_fingerprint="test_device",
            account_name="Test Account",
        )
        print("[SUCCESS] Valid LoginRequest created")
        return True

    except Exception as e:
        print(f"[ERROR] LoginRequest validation failed: {e}")
        return False


if __name__ == "__main__":
    print("Testing Private Key Authentication Functions")
    print("=" * 50)

    # Test login request validation
    validation_ok = test_login_request_validation()

    # Test agent wallet binding
    binding_ok = test_bind_agent_wallet()

    if validation_ok and binding_ok:
        print("\n[SUCCESS] All authentication functions are working!")
    else:
        print("\n[FAILED] Some authentication functions need debugging.")
