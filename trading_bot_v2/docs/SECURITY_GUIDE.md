# Trading Bot v2 - Security Guide

## Overview

This guide covers security best practices, configuration guidelines, and procedures for securing the Trading Bot v2 deployment. Security is paramount when dealing with financial systems and API credentials.

---

## Security Architecture

### Defense in Depth

```
┌─────────────────────────────────────────────────────────────┐
│                    Security Layers                           │
├─────────────────────────────────────────────────────────────┤
│  Layer 1: Network Security                                   │
│  ├── Firewall rules                                          │
│  ├── Fail2Ban intrusion prevention                          │
│  └── TLS/SSL encryption                                     │
├─────────────────────────────────────────────────────────────┤
│  Layer 2: Application Security                               │
│  ├── Input validation                                        │
│  ├── Rate limiting                                          │
│  └── Circuit breaker protection                             │
├─────────────────────────────────────────────────────────────┤
│  Layer 3: API Security                                       │
│  ├── HMAC-SHA256 signing                                    │
│  ├── Request authentication                                 │
│  └── Replay attack prevention                               │
├─────────────────────────────────────────────────────────────┤
│  Layer 4: Data Security                                      │
│  ├── Encrypted credentials                                  │
│  ├── Secure database                                        │
│  └── Audit logging                                          │
├─────────────────────────────────────────────────────────────┤
│  Layer 5: Operational Security                               │
│  ├── Least privilege principle                              │
│  ├── Regular backups                                        │
│  └── Monitoring & alerting                                  │
└─────────────────────────────────────────────────────────────┘
```

---

## Credential Management

### Environment Variables

All sensitive credentials are stored in environment variables, never in code:

```bash
# .env file - Restricted permissions (chmod 600)
AGENT_WALLET_PRIVATE_KEY=your_private_key_here
ACCOUNT_PUBLIC_KEY=your_public_key_here
```

**Security Requirements**:
- File permissions: `600` (owner read/write only)
- Never commit to version control
- Use `.gitignore` to exclude `.env` files
- Rotate keys quarterly

### Private Key Protection

```python
# config.py - Secure key loading
import os
from pathlib import Path
from dotenv import load_dotenv

class Config:
    def __init__(self):
        # Load from .env file
        env_path = Path(__file__).parent / '.env'
        load_dotenv(env_path)
        
        # Load credentials from environment only
        self.pacifica_private_key = os.getenv('AGENT_WALLET_PRIVATE_KEY')
        self.pacifica_public_key = os.getenv('ACCOUNT_PUBLIC_KEY')
        
        # Validate credentials exist
        if not self.pacifica_private_key:
            raise ValueError("AGENT_WALLET_PRIVATE_KEY not set")
```

### Key Rotation Procedure

1. **Generate new keys**:
   ```bash
   # On Pacifica platform
   # 1. Navigate to API key management
   # 2. Generate new key pair
   # 3. Copy new private and public keys
   ```

2. **Update configuration**:
   ```bash
   # Backup current .env
   cp .env .env.backup.$(date +%Y%m%d)
   
   # Update with new keys
   sed -i 's/AGENT_WALLET_PRIVATE_KEY=.*/AGENT_WALLET_PRIVATE_KEY=new_key/' .env
   sed -i 's/ACCOUNT_PUBLIC_KEY=.*/ACCOUNT_PUBLIC_KEY=new_key/' .env
   ```

3. **Restart service**:
   ```bash
   sudo systemctl restart trading-bot
   ```

4. **Verify operation**:
   ```bash
   curl http://localhost:8000/api/status
   ```

5. **Revoke old keys** (after 24-hour grace period):
   ```bash
   # On Pacifica platform
   # Revoke previous key pair
   ```

---

## API Request Security

### HMAC-SHA256 Signing

All API requests to Pacifica are signed using HMAC-SHA256:

```python
import hmac
import hashlib
import time

class PacificaClient:
    def _sign_request(self, method: str, endpoint: str, body: str = "") -> dict:
        """Sign API request with HMAC-SHA256."""
        timestamp = str(int(time.time() * 1000))
        
        # Create message to sign
        message = f"{timestamp}{method.upper()}{endpoint}{body}"
        
        # Generate signature
        signature = hmac.new(
            self.private_key.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        return {
            'X-Timestamp': timestamp,
            'X-Signature': signature,
            'X-API-Key': self.public_key
        }
```

### Request Security Features

1. **Timestamp Validation**: Requests include timestamps to prevent replay attacks
2. **Signature Verification**: Every request is cryptographically signed
3. **No Credentials in URL**: Keys never appear in request URLs
4. **HTTPS Only**: All API communications use TLS encryption

---

## Input Validation & Sanitization

### API Input Validation

```python
from pydantic import BaseModel, Field, validator
from typing import Optional

class TradeRequest(BaseModel):
    """Validated trade request model."""
    symbol: str = Field(..., min_length=3, max_length=20)
    side: str = Field(..., regex="^(buy|sell)$")
    quantity: float = Field(..., gt=0)
    price: Optional[float] = Field(None, gt=0)
    
    @validator('symbol')
    def validate_symbol(cls, v):
        """Validate trading symbol format."""
        allowed_chars = set('ABCDEFGHIJKLMNOPQRSTUVWXYZ/')
        if not all(c in allowed_chars for c in v.upper()):
            raise ValueError('Invalid symbol format')
        return v.upper()
    
    @validator('quantity')
    def validate_quantity(cls, v):
        """Validate quantity is reasonable."""
        if v > 1000:  # Maximum position size
            raise ValueError('Quantity exceeds maximum allowed')
        return v
```

### SQL Injection Prevention

```python
# database.py - Parameterized queries only
async def get_position(self, symbol: str) -> Optional[dict]:
    """Get position with parameterized query."""
    query = """
        SELECT * FROM positions 
        WHERE symbol = ? AND status = 'open'
    """
    # Never use f-strings or string concatenation for SQL
    async with self.conn.execute(query, (symbol,)) as cursor:
        return await cursor.fetchone()
```

---

## Rate Limiting & DoS Protection

### API Rate Limiting

```python
from fastapi import Request, HTTPException
import time
from collections import defaultdict

class RateLimiter:
    """Rate limiting middleware."""
    
    def __init__(self, requests_per_minute: int = 100):
        self.requests_per_minute = requests_per_minute
        self.requests = defaultdict(list)
    
    async def check_rate_limit(self, request: Request):
        """Check if request is within rate limit."""
        client_ip = request.client.host
        current_time = time.time()
        
        # Clean old requests
        self.requests[client_ip] = [
            req_time for req_time in self.requests[client_ip]
            if current_time - req_time < 60
        ]
        
        # Check limit
        if len(self.requests[client_ip]) >= self.requests_per_minute:
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded. Try again later."
            )
        
        # Record request
        self.requests[client_ip].append(current_time)
```

### Circuit Breaker Protection

```python
from error_handling import with_circuit_breaker

class PacificaClient:
    @with_circuit_breaker(
        name="pacifica_api",
        failure_threshold=5,
        recovery_timeout=60.0
    )
    async def place_order(self, symbol: str, side: str, quantity: float):
        """Place order with circuit breaker protection."""
        # API call implementation
        pass
```

---

## Network Security

### Firewall Configuration

```bash
# UFW (Uncomplicated Firewall) configuration

# Reset to defaults
sudo ufw --force reset

# Default policies
sudo ufw default deny incoming
sudo ufw default allow outgoing

# Allow SSH (adjust port as needed)
sudo ufw allow 22/tcp

# Allow trading bot web interface
sudo ufw allow 8000/tcp

# Allow HTTPS for production
sudo ufw allow 443/tcp

# Enable firewall
sudo ufw enable

# Check status
sudo ufw status verbose
```

### Fail2Ban Configuration

```bash
# /etc/fail2ban/jail.local
[DEFAULT]
bantime = 3600
findtime = 600
maxretry = 3

[trading-bot]
enabled = true
port = 8000
filter = trading-bot
logpath = /opt/trading-bot/logs/api.log
maxretry = 5
action = iptables-multiport[name=trading-bot, port="8000", protocol=tcp]
```

---

## TLS/SSL Configuration

### Certificate Setup

```bash
# Using Let's Encrypt (Production)
sudo apt install certbot

# Obtain certificate
sudo certbot certonly --standalone -d tradingbot.yourdomain.com

# Auto-renewal (already configured by certbot)
sudo certbot renew --dry-run
```

### HTTPS Configuration

```python
# api_server.py - HTTPS configuration
import ssl
from fastapi import FastAPI

app = FastAPI()

# SSL context for HTTPS
ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ssl_context.load_cert_chain(
    '/etc/letsencrypt/live/tradingbot.yourdomain.com/fullchain.pem',
    '/etc/letsencrypt/live/tradingbot.yourdomain.com/privkey.pem'
)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        ssl_keyfile="/etc/letsencrypt/live/tradingbot.yourdomain.com/privkey.pem",
        ssl_certfile="/etc/letsencrypt/live/tradingbot.yourdomain.com/fullchain.pem"
    )
```

---

## Audit Logging

### Security Event Logging

```python
import logging
from datetime import datetime
from typing import Dict, Any

class SecurityAuditLogger:
    """Security-focused audit logging."""
    
    def __init__(self):
        self.logger = logging.getLogger('security_audit')
        
    def log_auth_attempt(self, success: bool, ip: str, username: str = None):
        """Log authentication attempt."""
        event = {
            'timestamp': datetime.utcnow().isoformat(),
            'event_type': 'authentication',
            'success': success,
            'source_ip': ip,
            'username': username,
            'severity': 'high' if not success else 'info'
        }
        self.logger.warning(json.dumps(event) if not success else json.dumps(event))
    
    def log_api_call(self, endpoint: str, method: str, ip: str, status_code: int):
        """Log API call."""
        event = {
            'timestamp': datetime.utcnow().isoformat(),
            'event_type': 'api_call',
            'endpoint': endpoint,
            'method': method,
            'source_ip': ip,
            'status_code': status_code
        }
        self.logger.info(json.dumps(event))
    
    def log_trade(self, trade_details: Dict[str, Any], ip: str):
        """Log trade execution."""
        event = {
            'timestamp': datetime.utcnow().isoformat(),
            'event_type': 'trade_execution',
            'source_ip': ip,
            'trade': trade_details
        }
        self.logger.info(json.dumps(event))
```

### Audit Log Retention

```bash
# /etc/logrotate.d/trading-bot-audit
/opt/trading-bot/logs/audit.log {
    daily
    rotate 90  # Keep 90 days
    compress
    delaycompress
    missingok
    notifempty
    create 600 tradingbot tradingbot
    dateext
    dateformat -%Y%m%d
}
```

---

## Access Control

### User Permissions

```bash
# Create dedicated trading bot user
sudo useradd -r -s /bin/false tradingbot

# Set directory permissions
sudo chown -R tradingbot:tradingbot /opt/trading-bot
sudo chmod 750 /opt/trading-bot
sudo chmod 600 /opt/trading-bot/.env
sudo chmod 755 /opt/trading-bot/data
sudo chmod 755 /opt/trading-bot/logs
```

### Sudo Access Restrictions

```bash
# /etc/sudoers.d/trading-bot
# Allow specific commands only

# Service management
tradingbot ALL=(root) NOPASSWD: /bin/systemctl restart trading-bot

# Log viewing
tradingbot ALL=(root) NOPASSWD: /usr/bin/tail /var/log/trading-bot/*.log
```

---

## Database Security

### SQLite Security

```python
# database.py - Secure database operations
import sqlite3
import os
from pathlib import Path

class DatabaseManager:
    def __init__(self, db_path: str = None):
        self.db_path = db_path or os.getenv('DATABASE_PATH', 'data/trading_bot.db')
        
        # Ensure secure permissions on database file
        db_file = Path(self.db_path)
        if db_file.exists():
            # Set restrictive permissions (owner read/write only)
            os.chmod(db_file, 0o600)
    
    async def execute_secure(self, query: str, params: tuple = None):
        """Execute query with security checks."""
        # Validate query doesn't contain dangerous operations
        dangerous_keywords = ['DROP', 'DELETE', 'TRUNCATE', 'ALTER']
        query_upper = query.upper()
        
        for keyword in dangerous_keywords:
            if keyword in query_upper and 'WHERE' not in query_upper:
                raise SecurityError(f"Dangerous query detected: {keyword}")
        
        # Execute with parameterized query
        async with self.get_connection() as conn:
            return await conn.execute(query, params or ())
```

### Backup Encryption

```bash
#!/bin/bash
# backup_with_encryption.sh

BACKUP_DIR="/opt/backups/$(date +%Y%m%d)"
DB_FILE="/opt/trading-bot/data/trading_bot.db"

# Create backup
sqlite3 $DB_FILE ".backup '$BACKUP_DIR/trading_bot.db'"

# Encrypt backup with GPG
gpg --symmetric --cipher-algo AES256 \
    --output "$BACKUP_DIR/trading_bot.db.gpg" \
    "$BACKUP_DIR/trading_bot.db"

# Remove unencrypted backup
rm "$BACKUP_DIR/trading_bot.db"

# Set permissions
chmod 600 "$BACKUP_DIR/trading_bot.db.gpg"

echo "Encrypted backup created: $BACKUP_DIR/trading_bot.db.gpg"
```

---

## Security Monitoring

### Intrusion Detection

```python
# security_monitor.py
import asyncio
from datetime import datetime, timedelta
from collections import defaultdict

class SecurityMonitor:
    """Monitor for security threats."""
    
    def __init__(self):
        self.failed_logins = defaultdict(list)
        self.suspicious_ips = set()
    
    async def monitor(self):
        """Continuous security monitoring."""
        while True:
            await self._check_failed_logins()
            await self._check_unusual_patterns()
            await asyncio.sleep(60)
    
    async def _check_failed_logins(self):
        """Check for brute force attempts."""
        current_time = datetime.now()
        
        for ip, attempts in self.failed_logins.items():
            # Clean old attempts
            recent_attempts = [
                t for t in attempts
                if current_time - t < timedelta(minutes=5)
            ]
            
            if len(recent_attempts) > 5:
                # Alert on potential brute force
                await self._alert_security_team(
                    f"Potential brute force from IP: {ip}"
                )
                self.suspicious_ips.add(ip)
    
    async def _check_unusual_patterns(self):
        """Check for unusual trading patterns."""
        # Implementation for detecting unusual activity
        pass
```

### Alert Configuration

```python
# alerts.py
from dataclasses import dataclass
from enum import Enum

class AlertSeverity(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

@dataclass
class SecurityAlert:
    name: str
    severity: AlertSeverity
    condition: str
    action: str

# Define security alerts
SECURITY_ALERTS = [
    SecurityAlert(
        name="Multiple Failed Logins",
        severity=AlertSeverity.HIGH,
        condition="failed_logins > 5 in 5 minutes",
        action="block_ip_temporarily"
    ),
    SecurityAlert(
        name="Unusual Trading Volume",
        severity=AlertSeverity.MEDIUM,
        condition="trade_volume > 3x average",
        action="notify_admin"
    ),
    SecurityAlert(
        name="Large Withdrawal",
        severity=AlertSeverity.CRITICAL,
        condition="withdrawal_amount > $10000",
        action="require_manual_approval"
    )
]
```

---

## Incident Response

### Security Incident Procedure

1. **Detection**:
   ```bash
   # Check security logs
   sudo tail -f /opt/trading-bot/logs/security.log | grep "ALERT"
   
   # Monitor failed authentication
   sudo grep "authentication.*false" /opt/trading-bot/logs/security.log
   ```

2. **Containment**:
   ```bash
   # Block suspicious IP immediately
   sudo iptables -A INPUT -s SUSPICIOUS_IP -j DROP
   
   # Stop trading bot if compromised
   sudo systemctl stop trading-bot
   ```

3. **Investigation**:
   ```bash
   # Collect forensic data
   sudo cp /opt/trading-bot/logs/security.log /tmp/forensics/
   sudo cp /opt/trading-bot/data/trading_bot.db /tmp/forensics/
   
   # Check for unauthorized access
   sudo last -a
   sudo cat /var/log/auth.log
   ```

4. **Recovery**:
   ```bash
   # Rotate all API keys
   # Restore from known-good backup
   # Verify system integrity
   ```

### Emergency Contacts

```
Security Team: security@yourcompany.com
On-Call Engineer: +1-xxx-xxx-xxxx
Exchange Support: support@pacifica.fi
```

---

## Compliance Checklist

### Pre-Deployment Security Checklist

- [ ] API keys stored in environment variables only
- [ ] `.env` file has 600 permissions
- [ ] Database has 600 permissions
- [ ] Firewall configured and enabled
- [ ] Fail2Ban installed and configured
- [ ] TLS/SSL certificates installed (production)
- [ ] Audit logging enabled
- [ ] Rate limiting configured
- [ ] Input validation implemented
- [ ] Circuit breakers configured
- [ ] Dedicated service user created
- [ ] Regular backups configured
- [ ] Backup encryption enabled
- [ ] Log rotation configured
- [ ] Security monitoring active

### Regular Security Tasks

**Daily**:
- [ ] Review security logs for anomalies
- [ ] Check for failed login attempts
- [ ] Verify system integrity

**Weekly**:
- [ ] Review access logs
- [ ] Check for unusual trading patterns
- [ ] Verify backup success

**Monthly**:
- [ ] Rotate API keys
- [ ] Review user access
- [ ] Update dependencies
- [ ] Security scan

**Quarterly**:
- [ ] Full security audit
- [ ] Penetration testing
- [ ] Disaster recovery drill
- [ ] Policy review

---

**For security incidents or questions, contact the security team immediately.**
