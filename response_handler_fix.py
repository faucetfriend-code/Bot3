#!/usr/bin/env python3
"""
Fix for the critical '"success"' error in trading bot execution.

Root Cause: The Pacifica API is returning the string "success" instead of the expected 
JSON object format {"success": true, "data": {...}}. This causes the trading bot to fail
when trying to process the response.

Solution: Add robust response validation and handling for unexpected response formats.
"""

import json
import logging
from typing import Dict, Any, Optional, Union

logger = logging.getLogger(__name__)

class ResponseHandler:
    """Handles Pacifica API responses with robust validation and error handling."""
    
    @staticmethod
    def validate_order_response(response: Any) -> Dict[str, Any]:
        """
        Validate and normalize order response from Pacifica API.
        
        Args:
            response: Raw response from API (could be dict, string, etc.)
            
        Returns:
            Normalized response dict with expected format:
            {"success": bool, "data": dict, "error": Optional[str]}
        """
        logger.debug(f"Validating order response: {response} (type: {type(response)})")
        
        # Case 1: Response is already a dict (expected format)
        if isinstance(response, dict):
            return ResponseHandler._normalize_dict_response(response)
        
        # Case 2: Response is a string (could be JSON or just "success")
        elif isinstance(response, str):
            return ResponseHandler._handle_string_response(response)
        
        # Case 3: Response is None or unexpected type
        else:
            return {
                "success": False,
                "data": {},
                "error": f"Unexpected response type: {type(response).__name__}"
            }
    
    @staticmethod
    def _normalize_dict_response(response: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize dictionary response to expected format."""
        # Ensure we have the required fields
        normalized = {
            "success": bool(response.get("success", False)),
            "data": response.get("data", {}),
            "error": response.get("error") if not response.get("success") else None
        }
        
        # Validate data field
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
            else:
                # If parsed result is not a dict (e.g., just "success"), treat as success
                logger.warning(f"API returned non-dict JSON: {parsed}")
                return {
                    "success": True,
                    "data": {"raw_response": parsed},
                    "error": None
                }
        except json.JSONDecodeError:
            # Not valid JSON, treat raw string
            pass
        
        # Handle specific string responses
        if response.lower() == '"success"' or response.lower() == "success":
            logger.warning("API returned string 'success' instead of JSON object")
            return {
                "success": True,
                "data": {"status": "success"},
                "error": None
            }
        elif response.lower() == '"error"' or response.lower() == "error":
            logger.error("API returned string 'error'")
            return {
                "success": False,
                "data": {},
                "error": "API returned error response"
            }
        else:
            # Unknown string response
            logger.error(f"API returned unexpected string response: {response}")
            return {
                "success": False,
                "data": {},
                "error": f"Unexpected API response: {response}"
            }

def create_patched_execution_method():
    """
    Create a patched version of the order execution method with better error handling.
    """
    
    def safe_execute_order(self, signal, allocation_result, log_entry=None):
        """
        Safe order execution with robust response handling.
        """
        try:
            symbol = signal.asset
            capital_allocated = allocation_result.get("allocated_amount", 0)

            # Calculate quantity from allocated capital and entry price
            if signal.entry_price > 0 and capital_allocated > 0:
                quantity = capital_allocated / signal.entry_price
            else:
                quantity = 0

            logger.info(
                f"🔹 Executing {signal.strategy.name} signal for {symbol}: "
                f"{signal.side.name} {quantity:.6f} @ ${signal.entry_price:.4f} "
                f"(capital: ${capital_allocated:.2f})"
            )

            if quantity <= 0:
                logger.error(f"❌ Invalid quantity for {symbol}: {quantity}")
                self.signal_logger.log_signal_failed(
                    signal=signal,
                    error="Invalid quantity (<=0)",
                    notes=f"Capital: ${capital_allocated:.2f}, Price: ${signal.entry_price:.4f}",
                )
                return

            # Use refined signal if available
            refined_signal = signal
            if self.execution_layer is not None:
                try:
                    refined_signal = self.execution_layer.refine_entry(signal, symbol)
                    if refined_signal is None:
                        logger.info(f"⚠️ ExecutionLayer skipped entry for {symbol}")
                        self.signal_logger.log_signal_rejected(
                            signal=signal,
                            reason="ExecutionLayer timing skip",
                            notes="1m/5m timing conditions not met",
                        )
                        return
                    logger.debug(f"🎯 ExecutionLayer refined {symbol} entry")
                except AttributeError as e:
                    logger.warning(f"⚠️ ExecutionLayer method unavailable: {e}")
                except Exception as e:
                    logger.error(f"❌ ExecutionLayer refinement failed for {symbol}: {e}")
            
            # Use refined signal for execution
            signal = refined_signal
            side_str = "buy" if signal.side.name == "BUY" else "sell"

            # Place order with enhanced error handling
            try:
                order_response = self.client.place_order(
                    symbol=symbol,
                    side=side_str,
                    quantity=quantity,
                    order_type="market",
                )
            except Exception as api_error:
                logger.error(f"❌ API call failed for {symbol}: {api_error}")
                self.signal_logger.log_signal_failed(
                    signal=signal,
                    error=str(api_error),
                    notes="API call failed",
                )
                return

            # Log raw response for debugging
            logger.info(
                f"📡 Raw order response for {symbol}: type={type(order_response).__name__}, "
                f"value={str(order_response)[:200]}"
            )

            # Validate and normalize response using ResponseHandler
            validated_response = ResponseHandler.validate_order_response(order_response)
            
            # Extract order data
            order_data = validated_response.get("data", {})
            order_id = order_data.get("order_id") or order_data.get("id")

            # Create execution result
            execution_result = {
                "success": validated_response.get("success", False) and order_id is not None,
                "order_id": order_id,
                "executed_price": order_data.get("price", signal.entry_price),
                "error": validated_response.get("error"),
            }

            # Handle successful execution
            if execution_result.get("success"):
                logger.info(
                    f"✅ Order executed for {symbol}: "
                    f"ID={execution_result.get('order_id')}, "
                    f"Price=${execution_result.get('executed_price'):.4f}"
                )

                # Log successful execution
                self.signal_logger.log_signal_executed(
                    signal=signal,
                    order_id=str(execution_result.get("order_id", "")),
                    filled_price=execution_result.get("executed_price", 0),
                    filled_quantity=quantity,
                    execution_result="success",
                    notes=f"Capital allocated: ${capital_allocated:.2f}",
                )
            else:
                # Handle failed execution
                error_msg = execution_result.get("error", "Unknown error")
                logger.error(f"❌ Order execution failed for {symbol}: {error_msg}")
                self.signal_logger.log_signal_failed(
                    signal=signal,
                    error=error_msg,
                    notes=f"Order response: {validated_response}",
                )

        except Exception as e:
            logger.error(f"❌ Critical error in order execution for {signal.asset}: {e}", exc_info=True)
            self.signal_logger.log_signal_failed(
                signal=signal,
                error=str(e),
                notes="Critical error in execution pipeline",
            )
    
    return safe_execute_order

def create_patched_grid_orders_method():
    """
    Create a patched version of the grid orders method with better error handling.
    """
    
    def safe_place_grid_orders(self, signal, allocation_result, log_entry=None):
        """
        Safe grid order placement with robust response handling.
        """
        try:
            symbol = signal.asset
            
            # Capital extraction (existing logic)
            capital_keys = ["allocated_amount", "capital_allocated", "capital", "amount", "allocated"]
            capital = 0
            
            for key in capital_keys:
                if key in allocation_result and allocation_result[key] is not None:
                    try:
                        parsed_capital = float(allocation_result[key])
                        if parsed_capital > 0:
                            capital = parsed_capital
                            logger.info(f"💰 Found capital in '{key}': ${capital:.2f}")
                            break
                    except (ValueError, TypeError):
                        logger.warning(f"⚠️ Could not parse capital from '{key}': {allocation_result[key]}")
                        continue
            
            if capital <= 0:
                logger.error(f"❌ No capital allocated for {symbol}")
                return {"success": False, "error": f"No capital allocated (checked keys: {capital_keys})"}

            # Calculate grid levels
            grid_levels = self._calculate_grid_levels(signal=signal, total_capital=capital)
            if not grid_levels.get("buy_levels") or not grid_levels.get("sell_levels"):
                return {"success": False, "error": "Failed to calculate grid levels"}

            logger.info(f"📊 Grid levels calculated for {symbol}: {len(grid_levels['buy_levels'])} BUY, {len(grid_levels['sell_levels'])} SELL")

            # Place orders with enhanced error handling
            buy_order_ids = []
            sell_order_ids = []

            # Place BUY orders
            for level in grid_levels["buy_levels"]:
                try:
                    response = self.client.place_order(
                        symbol=symbol,
                        side="buy",
                        quantity=level["quantity"],
                        order_type="limit",
                        price=level["price"],
                    )

                    # Validate response using ResponseHandler
                    validated_response = ResponseHandler.validate_order_response(response)
                    order_data = validated_response.get("data", {})
                    order_id = order_data.get("order_id") or order_data.get("id")

                    if order_id and validated_response.get("success"):
                        buy_order_ids.append(str(order_id))
                        logger.info(f"  ✅ BUY order placed: {level['quantity']} @ ${level['price']:.4f} (ID: {order_id})")
                    else:
                        error_msg = validated_response.get("error", "Unknown error")
                        logger.error(f"  ❌ BUY order rejected: {error_msg}")

                except Exception as e:
                    logger.error(f"  ❌ Failed to place BUY order @ ${level['price']:.4f}: {e}")

            # Place SELL orders
            for level in grid_levels["sell_levels"]:
                try:
                    response = self.client.place_order(
                        symbol=symbol,
                        side="sell",
                        quantity=level["quantity"],
                        order_type="limit",
                        price=level["price"],
                    )

                    # Validate response using ResponseHandler
                    validated_response = ResponseHandler.validate_order_response(response)
                    order_data = validated_response.get("data", {})
                    order_id = order_data.get("order_id") or order_data.get("id")

                    if order_id and validated_response.get("success"):
                        sell_order_ids.append(str(order_id))
                        logger.info(f"  ✅ SELL order placed: {level['quantity']} @ ${level['price']:.4f} (ID: {order_id})")
                    else:
                        error_msg = validated_response.get("error", "Unknown error")
                        logger.error(f"  ❌ SELL order rejected: {error_msg}")

                except Exception as e:
                    logger.error(f"  ❌ Failed to place SELL order @ ${level['price']:.4f}: {e}")

            # Return results
            success = len(buy_order_ids) > 0 or len(sell_order_ids) > 0
            return {
                "success": success,
                "buy_orders": len(buy_order_ids),
                "sell_orders": len(sell_order_ids),
                "buy_order_ids": buy_order_ids,
                "sell_order_ids": sell_order_ids,
            }

        except Exception as e:
            logger.error(f"Error placing grid orders: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
    
    return safe_place_grid_orders

if __name__ == "__main__":
    print("=== Response Handler Fix for Trading Bot ===")
    print()
    print("This module provides:")
    print("1. ResponseHandler class for robust API response validation")
    print("2. Patched execution methods with enhanced error handling")
    print("3. Support for unexpected response formats like '\"success\"'")
    print()
    print("To apply the fix:")
    print("1. Import this module in trading_bot.py")
    print("2. Replace _execute_standard_signal_coordinated with safe_execute_order")
    print("3. Replace _place_grid_orders with safe_place_grid_orders")
    print("4. Add ResponseHandler.validate_order_response calls in all order processing")