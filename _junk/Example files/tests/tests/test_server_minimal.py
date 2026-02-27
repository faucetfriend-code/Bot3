#!/usr/bin/env python3
"""
Minimal test server for private key authentication (no database dependencies).
"""

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from auth import LoginRequest, TokenResponse, bind_agent_wallet, create_access_token
import uvicorn

app = FastAPI(title="Trading Bot Auth Test", version="1.0.0")

# Basic CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Store sessions in memory for testing
test_sessions = {}


@app.get("/", response_class=HTMLResponse)
async def get_interface():
    """Serve the HTML interface."""
    try:
        with open("trading_bot_interface.html", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "<h1>Interface file not found</h1>"


@app.post("/api/auth/login", response_model=TokenResponse)
async def login(request: LoginRequest):
    """Test authentication endpoint."""
    try:
        print(f"Login attempt with private key: {request.private_key[:20]}...")

        # For testing, use mock binding instead of real API call
        # Comment out the next line and uncomment the one after to test real binding
        result = mock_bind_agent_wallet(request.private_key, request.device_fingerprint)
        # result = bind_agent_wallet(request.private_key, request.device_fingerprint)

        if not result["success"]:
            raise HTTPException(
                status_code=401,
                detail=f"Agent wallet binding failed: {result['error']}",
            )

        # Create JWT token
        access_token = create_access_token(
            {
                "sub": request.account_name,
                "account_public_key": result["account_public_key"],
                "agent_wallet_public_key": result["agent_wallet_public_key"],
                "device_fingerprint": request.device_fingerprint,
            }
        )

        # Store session
        test_sessions[access_token] = {
            "account_public_key": result["account_public_key"],
            "agent_wallet_private_key": result["agent_wallet_private_key"],
            "agent_wallet_public_key": result["agent_wallet_public_key"],
            "account_name": request.account_name,
        }

        return TokenResponse(
            access_token=access_token,
            expires_in=30 * 60,
            account_name=request.account_name,
            agent_wallet=result["agent_wallet_public_key"],
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Authentication failed: {str(e)}")


def mock_bind_agent_wallet(private_key: str, device_fingerprint: str) -> dict:
    """FIX ME: Implement real authentication."""
    raise NotImplementedError("FIX ME: Implement real authentication")


@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "message": "Test server running"}


if __name__ == "__main__":
    print("Starting minimal test server...")
    print("Visit http://localhost:8000 to test the private key authentication")
    uvicorn.run("test_server_minimal:app", host="0.0.0.0", port=8000, reload=False)
