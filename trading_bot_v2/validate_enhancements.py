#!/usr/bin/env python3
"""
Validation script for trading bot v2 enhancements.

This script validates that the new enhancement components are properly
implemented and can be imported and initialized.
"""

import asyncio
import sys
import traceback
from pathlib import Path

# Add the trading_bot_v2 directory to Python path
sys.path.insert(0, str(Path(__file__).parent))

def test_imports():
    """Test that all enhancement modules can be imported."""
    print("Testing imports...")
    
    try:
        # Test task queue system
        from task_queue_system import (
            TaskPriority, TaskStatus, Task, TaskResult,
            TaskQueueSystem, get_task_queue_system
        )
        print("[OK] Task queue system imports successful")
        
        # Test performance monitor
        from performance_monitor import (
            PerformanceMonitor, MetricType, AlertSeverity,
            get_performance_monitor
        )
        print("[OK] Performance monitor imports successful")
        
        # Test error handling
        from error_handling import (
            ErrorCategory, ErrorSeverity, RetryPolicy,
            EnhancedCircuitBreaker, TaskError, TimeoutError,
            get_error_manager
        )
        print("[OK] Error handling imports successful")
        
        return True
        
    except Exception as e:
        print(f"[FAIL] Import failed: {e}")
        traceback.print_exc()
        return False

async def test_task_queue_system():
    """Test basic task queue functionality."""
    print("\nTesting task queue system...")
    
    try:
        from task_queue_system import get_task_queue_system, TaskPriority
        
        # Get task queue system
        task_queue = await get_task_queue_system()
        print("[OK] Task queue system created")
        
        # Test basic task submission
        async def test_task(x):
            return x * 2
        
        task_id = await task_queue.submit_task(
            test_task, 5,
            priority=TaskPriority.NORMAL,
            timeout=5.0
        )
        print(f"[OK] Task submitted: {task_id}")
        
        # Get queue stats
        stats = await task_queue.get_queue_stats()
        print(f"[OK] Queue stats: {stats['queue']['total']} tasks in queue")
        
        return True
        
    except Exception as e:
        print(f"[FAIL] Task queue test failed: {e}")
        traceback.print_exc()
        return False

async def test_performance_monitor():
    """Test basic performance monitoring functionality."""
    print("\nTesting performance monitor...")
    
    try:
        from performance_monitor import get_performance_monitor, MetricType
        
        # Get performance monitor
        monitor = await get_performance_monitor()
        print("[OK] Performance monitor created")
        
        # Test metric recording
        await monitor.metrics_collector.set_gauge("test.metric", 42.0)
        print("[OK] Gauge metric recorded")
        
        await monitor.metrics_collector.increment_counter("test.counter", 5)
        print("[OK] Counter metric recorded")
        
        # Test metric retrieval
        value = await monitor.metrics_collector.get_current_value("test.metric")
        if value == 42.0:
            print("[OK] Metric retrieval successful")
        else:
            print(f"[FAIL] Metric retrieval failed: expected 42.0, got {value}")
            return False
        
        return True
        
    except Exception as e:
        print(f"[FAIL] Performance monitor test failed: {e}")
        traceback.print_exc()
        return False

async def test_error_handling():
    """Test basic error handling functionality."""
    print("\nTesting error handling...")
    
    try:
        from error_handling import get_error_manager, ErrorCategory, TaskError
        
        # Get error manager
        error_manager = get_error_manager()
        print("[OK] Error manager created")
        
        # Test error handling
        test_error = TaskError("Test error", component="test", operation="test_func")
        context = await error_manager.handle_error(
            test_error,
            component="validation_script",
            operation="test_error_handling"
        )
        print(f"[OK] Error handled: {context.error_id}")
        
        # Test error statistics
        stats = await error_manager.get_error_stats()
        if stats['total_errors'] > 0:
            print("[OK] Error statistics working")
        else:
            print("[FAIL] Error statistics not working")
            return False
        
        return True
        
    except Exception as e:
        print(f"[FAIL] Error handling test failed: {e}")
        traceback.print_exc()
        return False

async def test_integration():
    """Test integration between components."""
    print("\nTesting integration...")
    
    try:
        # Test that components can work together
        from task_queue_system import get_task_queue_system, TaskPriority
        from performance_monitor import get_performance_monitor
        from error_handling import get_error_manager
        
        # Get all components
        task_queue = await get_task_queue_system()
        monitor = await get_performance_monitor()
        error_manager = get_error_manager()
        
        print("[OK] All components initialized")
        
        # Test task with error handling
        async def failing_task():
            raise ValueError("Test error for integration")
        
        task_id = await task_queue.submit_task(
            failing_task,
            priority=TaskPriority.LOW,
            timeout=2.0
        )
        
        # Wait a bit for processing
        await asyncio.sleep(0.1)
        
        print("[OK] Integration test completed")
        
        return True
        
    except Exception as e:
        print(f"[FAIL] Integration test failed: {e}")
        traceback.print_exc()
        return False

async def main():
    """Main validation function."""
    print("Starting Trading Bot v2 Enhancement Validation\n")
    
    # Test imports first
    if not test_imports():
        print("\n[FAIL] Validation failed: Import errors")
        return False
    
    # Test individual components
    test_results = []
    
    # Test task queue system
    test_results.append(await test_task_queue_system())
    
    # Test performance monitor
    test_results.append(await test_performance_monitor())
    
    # Test error handling
    test_results.append(await test_error_handling())
    
    # Test integration
    test_results.append(await test_integration())
    
    # Summary
    passed = sum(test_results)
    total = len(test_results)
    
    print(f"\nValidation Summary:")
    print(f"Passed: {passed}/{total}")
    
    if passed == total:
        print("All tests passed! Enhancements are ready for deployment.")
        return True
    else:
        print("Some tests failed. Please review the errors above.")
        return False

if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)