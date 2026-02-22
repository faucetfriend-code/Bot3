"""
ResponseHandler - Shared utility for handling Pacifica API responses.

Handles Pacifica's inconsistent API response formats:
- Dict with success/data/error fields (expected format)
- Boolean responses (direct API responses)
- String responses (e.g., "success", "error", '"success"', or JSON strings)
- None or unexpected types

Single shared module - used by trading_bot.py and grid_lifecycle_manager.py.
"""

import json
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)


class ResponseHandler:
    """Handles Pacifica API responses with robust validation and error handling."""

    @staticmethod
    def validate_order_response(response: Any) -> Dict[str, Any]:
        """
        Validate and normalize order response from Pacifica API.

        Args:
            response: Raw response from API (could be dict, string, bool, None)

        Returns:
            Normalized response dict: {"success": bool, "data": dict, "error": Optional[str]}
        """
        logger.debug(f"Validating order response: {response} (type: {type(response)})")

        # Defensive: Ensure response doesn't cause KeyError in string formatting
        if isinstance(response, dict):
            response.setdefault("success", False)
            response.setdefault("data", {})
            response.setdefault("error", None)

        # Case 1: Response is already a dict (expected format)
        if isinstance(response, dict):
            return ResponseHandler._normalize_dict_response(response)

        # Case 2: Response is a boolean (direct API response)
        elif isinstance(response, bool):
            logger.warning(f"API returned boolean directly: {response}")
            return {
                "success": response,
                "data": {"status": "success" if response else "error"},
                "error": None if response else "API returned false",
            }

        # Case 3: Response is a string (could be JSON or just "success")
        elif isinstance(response, str):
            return ResponseHandler._handle_string_response(response)

        # Case 4: Response is None or unexpected type
        else:
            return {
                "success": False,
                "data": {},
                "error": f"Unexpected response type: {type(response).__name__}",
            }

    @staticmethod
    def _normalize_dict_response(response: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize dictionary response to expected format."""
        normalized = {
            "success": bool(response.get("success", False)),
            "data": response.get("data", {}),
            "error": response.get("error") if not response.get("success") else None,
        }

        if not isinstance(normalized["data"], dict):
            logger.warning(f"Response data is not a dict: {normalized['data']}")
            normalized["data"] = {}

        return normalized

    @staticmethod
    def _handle_string_response(response: str) -> Dict[str, Any]:
        """Handle string response from API."""
        # Try to parse as JSON first
        try:
            parsed = json.loads(response)
            if isinstance(parsed, dict):
                return ResponseHandler._normalize_dict_response(parsed)
            elif isinstance(parsed, bool):
                return {
                    "success": parsed,
                    "data": {"status": "success" if parsed else "error"},
                    "error": None if parsed else "API returned false",
                }
            elif isinstance(parsed, str) and parsed.lower() == "success":
                return {
                    "success": True,
                    "data": {"status": "success"},
                    "error": None,
                }
            elif isinstance(parsed, str) and parsed.lower() == "error":
                return {
                    "success": False,
                    "data": {},
                    "error": "API returned error response",
                }
            else:
                return {
                    "success": True,
                    "data": {"raw_response": parsed},
                    "error": None,
                }
        except json.JSONDecodeError:
            pass

        # Handle specific string responses
        if response.lower() == '"success"' or response.lower() == "success":
            logger.warning("API returned string 'success' instead of JSON object")
            return {
                "success": True,
                "data": {"status": "success"},
                "error": None,
            }
        elif response.lower() == '"error"' or response.lower() == "error":
            logger.error("API returned string 'error'")
            return {
                "success": False,
                "data": {},
                "error": "API returned error response",
            }
        else:
            logger.error(f"API returned unexpected string response: {response}")
            return {
                "success": False,
                "data": {},
                "error": f"Unexpected API response: {response}",
            }
