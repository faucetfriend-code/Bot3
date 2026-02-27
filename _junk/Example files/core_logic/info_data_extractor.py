#!/usr/bin/env python3
"""
JSON Data Extraction Tool for Pacifica /info Endpoint
Extracts, validates, and transforms market data from API responses.
"""

import re
import logging
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)

class InfoDataExtractor:
    """
    Extract and validate data from Pacifica /info endpoint responses.

    Handles the JSON structure:
    {
        "success": true,
        "data": [...market objects...],
        "error": null,
        "code": null
    }
    """

    def __init__(self):
        """Initialize with validation rules."""
        self.required_fields = [
            'symbol', 'tick_size', 'lot_size', 'max_leverage',
            'funding_rate', 'next_funding_rate', 'min_order_size',
            'max_order_size', 'created_at'
        ]

        self.optional_fields = [
            'min_tick', 'max_tick', 'isolated_only'
        ]

        # Field validators
        self.field_validators = {
            'symbol': self._validate_symbol,
            'tick_size': self._validate_numeric_string,
            'min_tick': self._validate_numeric_string,
            'max_tick': self._validate_numeric_string,
            'lot_size': self._validate_numeric_string,
            'max_leverage': self._validate_positive_integer,
            'min_order_size': self._validate_numeric_string,
            'max_order_size': self._validate_numeric_string,
            'funding_rate': self._validate_numeric_string,
            'next_funding_rate': self._validate_numeric_string,
            'created_at': self._validate_timestamp,
            'isolated_only': self._validate_boolean
        }

    def extract_market_data(self, api_response: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Extract and validate market data from API response.

        Args:
            api_response: Raw API response dictionary

        Returns:
            List of validated market data dictionaries
        """
        try:
            # Validate response structure
            if not isinstance(api_response, dict):
                logger.error("API response is not a dictionary")
                return []

            if not api_response.get('success', False):
                logger.warning(f"API response indicates failure: {api_response.get('error')}")
                return []

            data = api_response.get('data', [])
            if not isinstance(data, list):
                logger.error("API response data is not a list")
                return []

            logger.info(f"Processing {len(data)} market records from API response")

            # Process each market
            validated_markets = []
            for i, market in enumerate(data):
                try:
                    validated_market = self._validate_market_record(market, i)
                    if validated_market:
                        validated_markets.append(validated_market)
                except Exception as e:
                    logger.warning(f"Failed to validate market record {i}: {e}")
                    continue

            logger.info(f"Successfully validated {len(validated_markets)} market records")
            return validated_markets

        except Exception as e:
            logger.error(f"Failed to extract market data: {e}")
            return []

    def _validate_market_record(self, market: Dict[str, Any], index: int) -> Optional[Dict[str, Any]]:
        """
        Validate a single market record.

        Args:
            market: Market data dictionary
            index: Record index for logging

        Returns:
            Validated market data or None if invalid
        """
        if not isinstance(market, dict):
            logger.warning(f"Market record {index} is not a dictionary")
            return None

        validated_record = {}

        # Check required fields
        for field in self.required_fields:
            if field not in market:
                logger.warning(f"Market record {index} missing required field: {field}")
                return None

            value = market[field]
            if not self._validate_field(field, value):
                logger.warning(f"Market record {index} invalid {field}: {value}")
                return None

            validated_record[field] = value

        # Check optional fields
        for field in self.optional_fields:
            if field in market:
                value = market[field]
                if self._validate_field(field, value):
                    validated_record[field] = value
                else:
                    logger.debug(f"Market record {index} optional field {field} invalid, skipping")

        return validated_record

    def _validate_field(self, field_name: str, value: Any) -> bool:
        """Validate a single field value."""
        validator = self.field_validators.get(field_name)
        if not validator:
            logger.warning(f"No validator for field: {field_name}")
            return True  # Allow unknown fields

        try:
            return validator(value)
        except Exception as e:
            logger.debug(f"Validation error for {field_name}={value}: {e}")
            return False

    # Field validators
    def _validate_symbol(self, value: str) -> bool:
        """Validate market symbol."""
        if not isinstance(value, str):
            return False
        # Allow 2-10 uppercase letters, numbers, and common symbols
        return bool(re.match(r'^[A-Z0-9]{2,10}$', value))

    def _validate_numeric_string(self, value: str) -> bool:
        """Validate numeric string (decimal or integer)."""
        if not isinstance(value, str):
            return False
        try:
            float(value)
            return True
        except ValueError:
            return False

    def _validate_positive_integer(self, value: int) -> bool:
        """Validate positive integer."""
        return isinstance(value, int) and value > 0

    def _validate_timestamp(self, value: int) -> bool:
        """Validate timestamp (reasonable range)."""
        if not isinstance(value, int):
            return False
        # Check if timestamp is reasonable (after 2020, before 2030)
        return 1577836800000 <= value <= 1893456000000  # 2020-2030 in milliseconds

    def _validate_boolean(self, value: bool) -> bool:
        """Validate boolean value."""
        return isinstance(value, bool)