# Pacifica Authentication

## Overview

Pacifica uses Agent Wallet authentication for secure API access on Solana. This system uses public/private key pairs for signing requests.

## Authentication Methods

### 1. Agent Wallet (Recommended for Bots)

Agent Wallets are Solana keypairs used to sign API requests. This is the primary method for programmatic trading.

**Requirements**:
- Solana keypair (public + private key)
- HMAC-SHA256 signing capability

**Key Generation**:
```python
from solders.keypair import Keypair

# Generate new keypair
keypair = Keypair()
private_key = str(keypair)  # Base58 encoded
public_key = str(keypair.pubkey())  # Base58 encoded

# Or load existing
keypair = Keypair.from_base58_string(private_key_str)
```

### 2. Hardware Wallet

For manual trading and account management through web interface. Not suitable for automated trading.

## REST API Authentication

### Request Signing

All authenticated REST API requests must include signature headers.

**Required Headers**:
```
X-API-Key: your_public_key
X-Signature: request_signature
X-Timestamp: unix_timestamp_ms
```

**Signature Generation**:

1. Create string to sign:
```
string_to_sign = f"{timestamp}{method}{path}{body}"
```

2. Sign with private key using HMAC-SHA256:
```python
import hmac
import hashlib
import time

def create_signature(private_key: str, method: str, path: str, body: str = "") -> dict:
    timestamp = int(time.time() * 1000)
    message = f"{timestamp}{method}{path}{body}"

    signature = hmac.new(
        private_key.encode(),
        message.encode(),
        hashlib.sha256
    ).hexdigest()

    return {
        "X-Timestamp": str(timestamp),
        "X-Signature": signature
    }
```

**Example Request**:
```python
import requests

# GET request
path = "/account"
method = "GET"
headers = {
    "X-API-Key": public_key,
    **create_signature(private_key, method, path)
}

response = requests.get(f"https://api.pacifica.fi{path}", headers=headers)
```

```python
# POST request with body
path = "/orders/market"
method = "POST"
body = json.dumps({"market": "BTC-PERP", "side": "buy", "size": 1.0})

headers = {
    "X-API-Key": public_key,
    "Content-Type": "application/json",
    **create_signature(private_key, method, path, body)
}

response = requests.post(f"https://api.pacifica.fi{path}", headers=headers, data=body)
```

## WebSocket Authentication

### Connection and Authentication

1. Connect to WebSocket
2. Send authentication message
3. Receive confirmation
4. Subscribe to private channels

**Authentication Message**:
```json
{
    "type": "authenticate",
    "api_key": "your_public_key",
    "signature": "hmac_signature",
    "timestamp": 1699999999
}
```

**Signature for WebSocket**:
```python
def create_ws_signature(private_key: str) -> dict:
    timestamp = int(time.time() * 1000)
    message = f"{timestamp}websocket"

    signature = hmac.new(
        private_key.encode(),
        message.encode(),
        hashlib.sha256
    ).hexdigest()

    return {
        "api_key": public_key,
        "signature": signature,
        "timestamp": timestamp
    }
```

**Example**:
```python
import asyncio
import json
import websockets

async def authenticate_ws():
    uri = "wss://ws.pacifica.fi"
    async with websockets.connect(uri) as ws:
        # Authenticate
        auth_data = create_ws_signature(private_key)
        await ws.send(json.dumps({
            "type": "authenticate",
            **auth_data
        }))

        # Wait for confirmation
        response = await ws.recv()
        auth_response = json.loads(response)

        if auth_response.get("success"):
            print("Authenticated successfully")
            # Now can subscribe to private channels
        else:
            print("Authentication failed")
```

## Security Best Practices

### Private Key Management

1. **Never Hardcode Keys**: Use environment variables
```python
import os
from dotenv import load_dotenv

load_dotenv()
private_key = os.getenv("AGENT_PRIVATE_KEY")
```

2. **Encrypt at Rest**: Use encryption for stored keys
```python
from encryption import encrypt_private_key, decrypt_private_key

# Store encrypted
encrypted = encrypt_private_key(private_key)

# Use decrypted
private_key = decrypt_private_key(encrypted)
```

3. **Limit Permissions**: Use subaccounts with limited funds for bots

4. **Rotate Keys**: Periodically generate new keypairs

5. **Monitor Usage**: Track API calls for unusual activity

### API Key Security

**DO**:
- Store in .env files (never commit to git)
- Use encryption for database storage
- Limit bot access to minimal required funds
- Use separate keys for dev/prod
- Monitor for unauthorized access

**DON'T**:
- Commit keys to version control
- Share keys in plain text
- Use same keys across environments
- Store unencrypted in databases
- Log keys in application logs

### Request Security

1. **Timestamp Validation**: Server rejects old timestamps (>5 seconds)
2. **Replay Protection**: Each request should use fresh timestamp
3. **HTTPS Only**: Never use HTTP for API calls
4. **Validate Responses**: Check signatures on critical responses

## Subaccounts

### Purpose

Subaccounts allow isolating funds and risk for different strategies or bots.

**Benefits**:
- Risk isolation per strategy
- Separate P&L tracking
- Limited bot access to funds
- Easy strategy performance comparison

### Creating Subaccounts

**API**: `POST /subaccounts`

```python
subaccount = client.create_subaccount(
    name="Trading Bot 1",
    description="Grid trading strategy"
)
```

### Transferring Funds

```python
# Transfer to subaccount
client.transfer_funds(
    from_account="main",
    to_account=subaccount_id,
    amount=5000.0
)
```

### Using Subaccount Keys

Each subaccount can have its own API keys for isolated bot access.

## Error Handling

### Authentication Errors

**Invalid Signature**:
```json
{
    "error": {
        "code": "INVALID_SIGNATURE",
        "message": "Request signature verification failed"
    }
}
```

**Causes**:
- Incorrect private key
- Wrong signature algorithm
- Malformed string to sign
- Body mismatch (for POST requests)

**Expired Timestamp**:
```json
{
    "error": {
        "code": "TIMESTAMP_EXPIRED",
        "message": "Request timestamp too old"
    }
}
```

**Fix**: Ensure system clock is synchronized

**Invalid API Key**:
```json
{
    "error": {
        "code": "INVALID_API_KEY",
        "message": "API key not found or disabled"
    }
}
```

## Integration with Project

### Current Implementation

The project already has encryption and authentication infrastructure:

**Files**:
- `encryption.py`: Fernet encryption for private keys
- `database.py`: Encrypted profile storage
- `api_server.py`: Profile management API

**Environment Variables** (.env):
```
AGENT_PRIVATE_KEY=your-private-key-here
MASTER_ENCRYPTION_KEY=your-encryption-key
ENCRYPTION_SALT=your-salt
```

### Using Stored Profiles

```python
from database import DatabaseManager
from encryption import decrypt_private_key

# Get profile from database
db = DatabaseManager()
profile = db.get_profile_by_name("Trading Bot")

# Decrypt private key
private_key = decrypt_private_key(profile["private_key_encrypted"])

# Use for API calls
headers = create_signature(private_key, "GET", "/account")
```

## Testing Authentication

### Test Endpoint

Use the account info endpoint to verify authentication:

```python
def test_authentication(public_key: str, private_key: str) -> bool:
    try:
        headers = {
            "X-API-Key": public_key,
            **create_signature(private_key, "GET", "/account")
        }

        response = requests.get(
            "https://api.pacifica.fi/account",
            headers=headers
        )

        return response.status_code == 200
    except Exception as e:
        print(f"Authentication test failed: {e}")
        return False
```

## Rate Limits by Authentication

Different rate limits apply based on account tier/volume:

- **Basic**: 60 requests/minute
- **Advanced**: 120 requests/minute
- **VIP**: 300 requests/minute

See `rate_limits.md` for detailed information.

## Additional Resources

- See `api_reference_rest.md` for endpoint details
- See `api_reference_websocket.md` for WebSocket setup
- See `error_codes.md` for authentication error codes
- Solders library: https://github.com/kevinheavey/solders
