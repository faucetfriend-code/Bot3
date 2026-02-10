#!/usr/bin/env python3
"""
Audit Logging System for Trading Bot.
Provides comprehensive audit trails for compliance, security monitoring, and forensic analysis.

Features:
- Tamper-proof audit logs with cryptographic hashing
- Comprehensive event logging (auth, trades, API calls, config changes)
- Compliance reporting and querying capabilities
- Integration with monitoring and alerting systems
"""

import os
import json
import hashlib
import sqlite3
import asyncio
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Union
from enum import Enum
from dataclasses import dataclass, asdict
from pathlib import Path
import logging
from loguru import logger

from database import get_db_connection
from monitoring import AlertManager, AlertLevel, AlertChannel
from encryption import EncryptionManager


class AuditEventType(Enum):
    """Types of auditable events."""

    # Authentication & Authorization
    AUTH_LOGIN = "auth.login"
    AUTH_LOGOUT = "auth.logout"
    AUTH_FAILED = "auth.failed"
    AUTH_TOKEN_REFRESH = "auth.token_refresh"
    AUTH_PERMISSION_CHANGE = "auth.permission_change"

    # Trading Operations
    TRADE_EXECUTED = "trade.executed"
    TRADE_CANCELLED = "trade.cancelled"
    TRADE_MODIFIED = "trade.modified"
    ORDER_PLACED = "order.placed"
    ORDER_CANCELLED = "order.cancelled"
    POSITION_OPENED = "position.opened"
    POSITION_CLOSED = "position.closed"
    POSITION_MODIFIED = "position.modified"

    # API Operations
    API_REQUEST = "api.request"
    API_RESPONSE = "api.response"
    API_ERROR = "api.error"
    API_RATE_LIMIT = "api.rate_limit"

    # Configuration Changes
    CONFIG_CHANGED = "config.changed"
    CONFIG_BACKUP = "config.backup"
    CONFIG_RESTORE = "config.restore"

    # Security Events
    SECURITY_KEY_ROTATED = "security.key_rotated"
    SECURITY_IP_WHITELIST = "security.ip_whitelist"
    SECURITY_ENCRYPTION = "security.encryption"
    SECURITY_ACCESS_DENIED = "security.access_denied"

    # System Events
    SYSTEM_STARTUP = "system.startup"
    SYSTEM_SHUTDOWN = "system.shutdown"
    SYSTEM_BACKUP = "system.backup"
    SYSTEM_RESTORE = "system.restore"
    SYSTEM_ERROR = "system.error"

    # Data Operations
    DATA_EXPORT = "data.export"
    DATA_IMPORT = "data.import"
    DATA_MODIFIED = "data.modified"
    DATA_DELETED = "data.deleted"


class AuditSeverity(Enum):
    """Severity levels for audit events."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class AuditEvent:
    """Audit event data structure."""

    id: str
    timestamp: datetime
    event_type: AuditEventType
    severity: AuditSeverity
    user_id: Optional[str]
    session_id: Optional[str]
    ip_address: Optional[str]
    user_agent: Optional[str]
    resource: str  # What was accessed/modified
    action: str    # What action was performed
    status: str    # success, failure, error
    details: Dict[str, Any]
    metadata: Dict[str, Any]
    hash_chain: str  # For tamper detection


class AuditLogger:
    """
    Comprehensive audit logging system with tamper-proof capabilities.
    """

    def __init__(self, db_path: str = None, encryption_key: str = None):
        self.db_path = db_path or os.getenv("AUDIT_DB_PATH", "./data/audit.db")
        self.encryption = EncryptionManager(encryption_key) if encryption_key else None
        self._lock = threading.Lock()
        self._last_hash = self._get_last_hash()

        # Ensure audit database exists
        self._init_audit_database()

        # Alert manager for security events
        self.alert_manager = AlertManager()

    def _init_audit_database(self):
        """Initialize audit database and tables."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_events (
                    id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    user_id TEXT,
                    session_id TEXT,
                    ip_address TEXT,
                    user_agent TEXT,
                    resource TEXT NOT NULL,
                    action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    details TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    hash_chain TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
            """)

            # Create indexes for efficient querying
            conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON audit_events(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_event_type ON audit_events(event_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_user_id ON audit_events(user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_severity ON audit_events(severity)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_resource ON audit_events(resource)")

            conn.commit()

    def _get_last_hash(self) -> str:
        """Get the hash of the last audit event for chain integrity."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("""
                    SELECT hash_chain FROM audit_events
                    ORDER BY created_at DESC LIMIT 1
                """)
                result = cursor.fetchone()
                return result[0] if result else ""
        except Exception:
            return ""

    def _calculate_event_hash(self, event_data: Dict[str, Any]) -> str:
        """Calculate cryptographic hash for event integrity."""
        # Create canonical JSON representation
        canonical_data = json.dumps(event_data, sort_keys=True, default=str)

        # Include previous hash for chain integrity
        if self._last_hash:
            canonical_data += self._last_hash

        # Calculate SHA-256 hash
        return hashlib.sha256(canonical_data.encode()).hexdigest()

    def log_event(
        self,
        event_type: AuditEventType,
        severity: AuditSeverity,
        resource: str,
        action: str,
        status: str = "success",
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        alert_on_critical: bool = True
    ) -> str:
        """
        Log an audit event with tamper-proof hashing.

        Returns the event ID.
        """
        import uuid
        event_id = str(uuid.uuid4())
        timestamp = datetime.now()

        # Prepare event data
        event_data = {
            "id": event_id,
            "timestamp": timestamp.isoformat(),
            "event_type": event_type.value,
            "severity": severity.value,
            "user_id": user_id,
            "session_id": session_id,
            "ip_address": ip_address,
            "user_agent": user_agent,
            "resource": resource,
            "action": action,
            "status": status,
            "details": details or {},
            "metadata": metadata or {}
        }

        # Calculate hash chain
        hash_chain = self._calculate_event_hash(event_data)

        # Add hash to event data
        event_data["hash_chain"] = hash_chain

        # Encrypt sensitive data if encryption is enabled
        if self.encryption and self._contains_sensitive_data(event_data):
            event_data = self._encrypt_sensitive_fields(event_data)

        # Store event
        with self._lock:
            try:
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute("""
                        INSERT INTO audit_events
                        (id, timestamp, event_type, severity, user_id, session_id,
                         ip_address, user_agent, resource, action, status, details,
                         metadata, hash_chain, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        event_id,
                        timestamp.isoformat(),
                        event_type.value,
                        severity.value,
                        user_id,
                        session_id,
                        ip_address,
                        user_agent,
                        resource,
                        action,
                        status,
                        json.dumps(event_data.get("details", {})),
                        json.dumps(event_data.get("metadata", {})),
                        hash_chain,
                        timestamp.timestamp()
                    ))
                    conn.commit()

                # Update last hash for chain
                self._last_hash = hash_chain

                logger.info(f"Audit event logged: {event_type.value} - {resource}:{action}")

                # Alert on critical security events
                if alert_on_critical and severity == AuditSeverity.CRITICAL:
                    task = asyncio.create_task(self._alert_critical_event(event_data))
                    task.add_done_callback(
                        lambda t: t.exception() and logger.error(f"Critical event alert task failed: {t.exception()}")
                    )

                return event_id

            except Exception as e:
                logger.error(f"Failed to log audit event: {e}")
                return ""

    def _contains_sensitive_data(self, event_data: Dict[str, Any]) -> bool:
        """Check if event data contains sensitive information."""
        sensitive_fields = ["password", "key", "secret", "token", "private_key", "api_key"]

        def check_dict(data: Dict[str, Any]) -> bool:
            for key, value in data.items():
                if any(sensitive in key.lower() for sensitive in sensitive_fields):
                    return True
                if isinstance(value, dict):
                    if check_dict(value):
                        return True
                elif isinstance(value, str) and any(sensitive in value.lower() for sensitive in sensitive_fields):
                    return True
            return False

        return check_dict(event_data)

    def _encrypt_sensitive_fields(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """Encrypt sensitive fields in event data."""
        if not self.encryption:
            return event_data

        def encrypt_dict(data: Dict[str, Any]) -> Dict[str, Any]:
            result = {}
            for key, value in data.items():
                if any(sensitive in key.lower() for sensitive in ["password", "key", "secret", "token", "private_key", "api_key"]):
                    if isinstance(value, str):
                        result[key] = self.encryption.encrypt(value)
                        result[f"{key}_encrypted"] = True
                    else:
                        result[key] = value
                elif isinstance(value, dict):
                    result[key] = encrypt_dict(value)
                else:
                    result[key] = value
            return result

        return encrypt_dict(event_data)

    async def _alert_critical_event(self, event_data: Dict[str, Any]):
        """Send alert for critical security events."""
        try:
            await self.alert_manager.send_alert(
                level=AlertLevel.CRITICAL,
                title=f"Critical Security Event: {event_data['event_type']}",
                message=f"Critical audit event detected: {event_data['action']} on {event_data['resource']}",
                source="audit_system",
                metadata={
                    "event_id": event_data["id"],
                    "event_type": event_data["event_type"],
                    "severity": event_data["severity"],
                    "user_id": event_data.get("user_id"),
                    "resource": event_data["resource"]
                }
            )
        except Exception as e:
            logger.error(f"Failed to send critical event alert: {e}")

    def query_events(
        self,
        event_type: Optional[AuditEventType] = None,
        user_id: Optional[str] = None,
        severity: Optional[AuditSeverity] = None,
        resource: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[AuditEvent]:
        """Query audit events with filtering options."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                query = "SELECT * FROM audit_events WHERE 1=1"
                params = []

                if event_type:
                    query += " AND event_type = ?"
                    params.append(event_type.value)

                if user_id:
                    query += " AND user_id = ?"
                    params.append(user_id)

                if severity:
                    query += " AND severity = ?"
                    params.append(severity.value)

                if resource:
                    query += " AND resource = ?"
                    params.append(resource)

                if start_date:
                    query += " AND timestamp >= ?"
                    params.append(start_date.isoformat())

                if end_date:
                    query += " AND timestamp <= ?"
                    params.append(end_date.isoformat())

                query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
                params.extend([limit, offset])

                cursor = conn.execute(query, params)
                rows = cursor.fetchall()

                events = []
                for row in rows:
                    try:
                        # Properly convert sqlite3.Row to dict using keys()
                        event_data = {key: row[key] for key in row.keys()}

                        # Remove database-only field (created_at) - not in AuditEvent dataclass
                        event_data.pop('created_at', None)

                        event_data["details"] = json.loads(event_data["details"]) if event_data["details"] else {}
                        event_data["metadata"] = json.loads(event_data["metadata"]) if event_data["metadata"] else {}

                        # Decrypt sensitive data if needed
                        if self.encryption:
                            event_data = self._decrypt_sensitive_fields(event_data)

                        events.append(AuditEvent(**event_data))
                    except Exception as e:
                        logger.warning(f"Failed to parse audit event {row[0]}: {e}")
                        continue

                return events

        except Exception as e:
            logger.error(f"Failed to query audit events: {e}")
            return []

    def _decrypt_sensitive_fields(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """Decrypt sensitive fields in event data."""
        if not self.encryption:
            return event_data

        def decrypt_dict(data: Dict[str, Any]) -> Dict[str, Any]:
            result = {}
            for key, value in data.items():
                if key.endswith("_encrypted") and data.get(key) is True:
                    original_key = key[:-10]  # Remove "_encrypted" suffix
                    if original_key in data:
                        try:
                            result[original_key] = self.encryption.decrypt(data[original_key])
                        except Exception:
                            result[original_key] = "[DECRYPTION_FAILED]"
                    result[key] = True
                elif isinstance(value, dict):
                    result[key] = decrypt_dict(value)
                else:
                    result[key] = value
            return result

        return decrypt_dict(event_data)

    def verify_integrity(self) -> Dict[str, Any]:
        """Verify the integrity of the audit log chain."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("""
                    SELECT id, timestamp, event_type, severity, user_id, session_id,
                           ip_address, user_agent, resource, action, status, details,
                           metadata, hash_chain, created_at
                    FROM audit_events ORDER BY created_at ASC
                """)

                rows = cursor.fetchall()
                expected_hash = ""
                verified_count = 0
                corrupted_events = []

                for row in rows:
                    event_data = {
                        "id": row[0],
                        "timestamp": row[1],
                        "event_type": row[2],
                        "severity": row[3],
                        "user_id": row[4],
                        "session_id": row[5],
                        "ip_address": row[6],
                        "user_agent": row[7],
                        "resource": row[8],
                        "action": row[9],
                        "status": row[10],
                        "details": json.loads(row[11]),
                        "metadata": json.loads(row[12])
                    }

                    # Calculate expected hash
                    canonical_data = json.dumps(event_data, sort_keys=True, default=str)
                    if expected_hash:
                        canonical_data += expected_hash
                    calculated_hash = hashlib.sha256(canonical_data.encode()).hexdigest()

                    # Check if hash matches
                    stored_hash = row[13]
                    if calculated_hash != stored_hash:
                        corrupted_events.append({
                            "event_id": row[0],
                            "expected_hash": calculated_hash,
                            "stored_hash": stored_hash
                        })
                    else:
                        verified_count += 1

                    expected_hash = calculated_hash

                return {
                    "total_events": len(rows),
                    "verified_events": verified_count,
                    "corrupted_events": corrupted_events,
                    "integrity_status": "valid" if len(corrupted_events) == 0 else "corrupted",
                    "corrupted_details": corrupted_events[:10]  # Limit details
                }

        except Exception as e:
            logger.error(f"Failed to verify audit log integrity: {e}")
            return {
                "error": str(e),
                "integrity_status": "error"
            }

    def generate_compliance_report(
        self,
        start_date: datetime,
        end_date: datetime,
        report_type: str = "summary"
    ) -> Dict[str, Any]:
        """Generate compliance report for the specified period."""
        try:
            events = self.query_events(
                start_date=start_date,
                end_date=end_date,
                limit=10000  # Large limit for reporting
            )

            report = {
                "report_period": {
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat()
                },
                "generated_at": datetime.now().isoformat(),
                "total_events": len(events)
            }

            if report_type == "summary":
                # Summary statistics
                event_counts = {}
                severity_counts = {}
                user_activity = {}
                resource_access = {}

                for event in events:
                    # Event type counts
                    event_counts[event.event_type.value] = event_counts.get(event.event_type.value, 0) + 1

                    # Severity counts
                    severity_counts[event.severity.value] = severity_counts.get(event.severity.value, 0) + 1

                    # User activity
                    if event.user_id:
                        user_activity[event.user_id] = user_activity.get(event.user_id, 0) + 1

                    # Resource access
                    resource_access[event.resource] = resource_access.get(event.resource, 0) + 1

                report.update({
                    "event_type_breakdown": event_counts,
                    "severity_breakdown": severity_counts,
                    "user_activity": user_activity,
                    "resource_access": resource_access
                })

            elif report_type == "detailed":
                # Detailed event list
                report["events"] = [
                    {
                        "id": event.id,
                        "timestamp": event.timestamp.isoformat(),
                        "event_type": event.event_type.value,
                        "severity": event.severity.value,
                        "user_id": event.user_id,
                        "resource": event.resource,
                        "action": event.action,
                        "status": event.status
                    }
                    for event in events
                ]

            # Integrity check
            integrity = self.verify_integrity()
            report["integrity_check"] = integrity

            return report

        except Exception as e:
            logger.error(f"Failed to generate compliance report: {e}")
            return {"error": str(e)}

    def close(self):
        """Close the audit logger and cleanup resources."""
        # SQLite connections are automatically closed when they go out of scope
        # but we can add any additional cleanup here if needed
        pass


# Global audit logger instance
audit_logger = AuditLogger()


# Convenience functions for common audit events
def audit_auth_success(user_id: str, session_id: str, ip_address: str = None) -> None:
    """Log successful authentication."""
    audit_logger.log_event(
        event_type=AuditEventType.AUTH_LOGIN,
        severity=AuditSeverity.LOW,
        resource="authentication",
        action="login",
        status="success",
        user_id=user_id,
        session_id=session_id,
        ip_address=ip_address
    )


def audit_auth_failure(user_id: str, reason: str, ip_address: str = None) -> None:
    """Log failed authentication."""
    audit_logger.log_event(
        event_type=AuditEventType.AUTH_FAILED,
        severity=AuditSeverity.MEDIUM,
        resource="authentication",
        action="login",
        status="failure",
        user_id=user_id,
        ip_address=ip_address,
        details={"reason": reason}
    )


def audit_trade_executed(trade_data: Dict[str, Any], user_id: str = None) -> None:
    """Log trade execution."""
    audit_logger.log_event(
        event_type=AuditEventType.TRADE_EXECUTED,
        severity=AuditSeverity.MEDIUM,
        resource=f"trade:{trade_data.get('symbol', 'unknown')}",
        action="execute",
        status="success",
        user_id=user_id,
        details=trade_data
    )


def audit_api_call(endpoint: str, method: str, status_code: int, user_id: str = None, ip_address: str = None) -> None:
    """Log API call."""
    severity = AuditSeverity.LOW if status_code < 400 else AuditSeverity.MEDIUM

    audit_logger.log_event(
        event_type=AuditEventType.API_REQUEST,
        severity=severity,
        resource=f"api:{endpoint}",
        action=method.lower(),
        status="success" if status_code < 400 else "error",
        user_id=user_id,
        ip_address=ip_address,
        details={"status_code": status_code, "method": method}
    )


def audit_config_change(config_key: str, old_value: Any, new_value: Any, user_id: str = None) -> None:
    """Log configuration change."""
    audit_logger.log_event(
        event_type=AuditEventType.CONFIG_CHANGED,
        severity=AuditSeverity.HIGH,
        resource=f"config:{config_key}",
        action="modify",
        status="success",
        user_id=user_id,
        details={
            "old_value": str(old_value) if old_value is not None else None,
            "new_value": str(new_value) if new_value is not None else None
        }
    )


def audit_security_event(event_type: str, resource: str, details: Dict[str, Any], user_id: str = None):
    """Log security-related event."""
    audit_logger.log_event(
        event_type=AuditEventType(event_type),
        severity=AuditSeverity.HIGH,
        resource=resource,
        action="security_event",
        status="info",
        user_id=user_id,
        details=details,
        alert_on_critical=True
    )