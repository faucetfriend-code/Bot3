# Pacifica API Implementation Notes

**Date:** 2025-12-04
**Purpose:** Document actual Pacifica API behavior vs. documentation

## Summary

This document records deviations between Pacifica's public documentation and actual API behavior discovered through testing.

---

## Authentication Method

### Test Results (2025-12-04)

**Public Endpoints:**
- ✅ Work without authentication
- Example: `GET /info` returns 200 with market data

**Authenticated Endpoints:**
- ❌ Ed25519 signature method: FAILED
- ❌ HMAC-SHA256 signature method: FAILED
- Both returned: `Query deserialize error: missing field 'account'`

**Current Issue:**
The API is rejecting authentication even though the `account` parameter is being sent. Error message suggests auth parameters need to be sent in a different format.

**Debug Evidence:**
```
Auth payload sent: {'account': '6Jj5ahJw...', 'signature': 'Uju4...', 'timestamp': 1764907623244, 'expiry_window': 5000}
Response: 400 Bad Request
Body: Query deserialize error: missing field `account`
```

The error says "Query deserialize" which suggests parameters should be in query string format, not request body.

---

## Endpoint Paths

### Confirmed Working Paths

| Endpoint Type | Path | Method | Auth Required | Status |
|---------------|------|--------|---------------|--------|
| Market Info | `/info` | GET | No | ✅ 200 OK |
| Funding Rates | `/info` | GET | No | ✅ 200 OK (included in market data) |

### Confirmed Existing (Auth Needed)

| Endpoint Type | Path | Method | Auth Required | Test Result |
|---------------|------|--------|---------------|-------------|
| Account Balance | `/account` | GET | Yes | 400 Bad Request |
| Positions | `/positions` | GET | Yes | 400 Bad Request |
| Orders | `/orders` | GET | Yes | 400 Bad Request |
| Order History | `/orders/history` | GET | Yes | 400 Bad Request |

**Note:** 400 (not 404) confirms these paths exist but authentication is being rejected.

### Paths That Don't Exist (404)

- `/account/info` -> 404
- `/balance` -> 404
- `/account/balance` -> 404
- `/account/positions` -> 404
- `/account/orders` -> 404
- `/fills` -> 404
- `/account/fills` -> 404

---

## Deviations from Documentation

### 1. Authentication Format

**Documentation Says:** Use HMAC-SHA256 with specific header format
**Actual Behavior:** Both Ed25519 and HMAC-SHA256 fail with same error
**Root Cause:** Unknown - parameters may need to be sent as query string instead of body

### 2. Endpoint Structure

**Documentation Says:** Various `/account/*` sub-paths
**Actual Behavior:** Endpoints are flat (`/account`, `/positions`, `/orders` etc.)
**Impact:** Using correct paths, but authentication still failing

---

## Next Steps

1. **Investigate Query String Auth:** Test sending auth parameters as URL query params
2. **Check Parameter Names:** Verify field names match API expectations
3. **Test Different Signatures:** Try different signature formats
4. **Contact Pacifica Support:** If issue persists, reach out for clarification

---

## Test Environment

- **Testnet URL:** `https://test-api.pacifica.fi/api/v1`
- **Mainnet URL:** `https://api.pacifica.fi/api/v1`
- **SSL Verification:** Disabled for testnet
- **Test Date:** 2025-12-04

---

## Code References

- Authentication implementation: `pacifica_client.py` line 241-318
- Test scripts: `tests/test_pacifica_auth.py`, `tests/test_api_paths.py`
- Documented auth method: `context files/pacifica/authentication.md`
