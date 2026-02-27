"""
Parallel Processing Utilities for Trading Bot.

Implements multiprocessing support for CPU-intensive operations
to overcome Python's GIL limitations and utilize multiple cores.

Key use cases:
- Batch indicator calculations
- Parallel backtesting
- Heavy data processing
"""

import asyncio
import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor
from typing import List, Dict, Any, Callable, Optional, Tuple
from functools import partial
import numpy as np
from loguru import logger

# Get optimal worker count (leave 1 core for OS)
MAX_WORKERS = max(1, mp.cpu_count() - 1)


class ParallelProcessor:
    """Manages parallel execution of CPU-intensive tasks across multiple processes."""

    def __init__(self, max_workers: Optional[int] = None):
        """
        Initialize parallel processor.

        Args:
            max_workers: Maximum number of worker processes (default: cpu_count - 1)
        """
        self.max_workers = max_workers or MAX_WORKERS
        logger.info(
            f"ParallelProcessor initialized with {self.max_workers} workers"
        )

    async def execute_parallel(
        self,
        func: Callable,
        data_chunks: List[Any],
        **kwargs
    ) -> List[Any]:
        """
        Execute function in parallel across multiple processes.

        Args:
            func: Function to execute (must be picklable)
            data_chunks: List of data chunks to process
            **kwargs: Additional keyword arguments to pass to func

        Returns:
            List of results from each chunk
        """
        if not data_chunks:
            return []

        # For small datasets, don't use multiprocessing (overhead not worth it)
        if len(data_chunks) < 10:
            logger.debug(
                f"Dataset too small ({len(data_chunks)} items), using single process"
            )
            return [func(chunk, **kwargs) for chunk in data_chunks]

        loop = asyncio.get_event_loop()

        with ProcessPoolExecutor(max_workers=self.max_workers) as executor:
            # Create partial function with fixed kwargs
            partial_func = partial(func, **kwargs) if kwargs else func

            # Submit all tasks
            futures = [
                loop.run_in_executor(executor, partial_func, chunk)
                for chunk in data_chunks
            ]

            # Gather results
            results = await asyncio.gather(*futures, return_exceptions=True)

            # Check for errors
            errors = [r for r in results if isinstance(r, Exception)]
            if errors:
                logger.error(
                    f"Parallel execution had {len(errors)} errors: {errors[0]}"
                )
                # Re-raise first error
                raise errors[0]

            return results

    async def map_parallel(
        self,
        func: Callable,
        items: List[Any],
        chunk_size: Optional[int] = None
    ) -> List[Any]:
        """
        Map function over items in parallel with automatic chunking.

        Args:
            func: Function to apply to each item
            items: List of items to process
            chunk_size: Size of chunks (default: auto-calculate)

        Returns:
            List of results
        """
        if not items:
            return []

        # Auto-calculate optimal chunk size
        if chunk_size is None:
            chunk_size = max(1, len(items) // (self.max_workers * 4))

        # Split into chunks
        chunks = [
            items[i:i + chunk_size]
            for i in range(0, len(items), chunk_size)
        ]

        logger.debug(
            f"Processing {len(items)} items in {len(chunks)} chunks "
            f"(size: {chunk_size})"
        )

        # Process chunks in parallel
        # Create partial function with func bound
        process_func = partial(self._process_chunk, func=func)
        chunk_results = await self.execute_parallel(
            process_func,
            chunks
        )

        # Flatten results
        results = []
        for chunk_result in chunk_results:
            results.extend(chunk_result)

        return results

    @staticmethod
    def _process_chunk(items: List[Any], func: Callable) -> List[Any]:
        """Process a chunk of items (executed in worker process)."""
        return [func(item) for item in items]


class ParallelIndicatorCalculator:
    """Calculates technical indicators in parallel for multiple symbols/timeframes."""

    def __init__(self, max_workers: Optional[int] = None):
        self.processor = ParallelProcessor(max_workers)

    async def calculate_indicators_batch(
        self,
        price_datasets: List[Dict[str, Any]],
        indicator_func: Callable,
        **indicator_kwargs
    ) -> List[Any]:
        """
        Calculate indicators for multiple datasets in parallel.

        Args:
            price_datasets: List of dicts with 'symbol' and 'prices' keys
            indicator_func: Indicator calculation function
            **indicator_kwargs: Arguments for indicator function

        Returns:
            List of indicator results
        """
        logger.info(
            f"Calculating indicators for {len(price_datasets)} datasets in parallel"
        )

        # Create calculation tasks
        tasks = [
            (dataset['prices'], indicator_kwargs)
            for dataset in price_datasets
        ]

        # Execute in parallel
        results = await self.processor.execute_parallel(
            self._calculate_single_indicator,
            tasks,
            indicator_func=indicator_func
        )

        return results

    @staticmethod
    def _calculate_single_indicator(
        task: Tuple[List[float], Dict[str, Any]],
        indicator_func: Callable
    ) -> Any:
        """Calculate indicator for single dataset (executed in worker process)."""
        prices, kwargs = task
        return indicator_func(prices, **kwargs)


class ParallelBacktestRunner:
    """Runs multiple backtests in parallel for parameter optimization."""

    def __init__(self, max_workers: Optional[int] = None):
        self.processor = ParallelProcessor(max_workers)

    async def run_backtests_parallel(
        self,
        backtest_configs: List[Any],
        backtest_func: Callable
    ) -> List[Any]:
        """
        Run multiple backtests in parallel.

        Args:
            backtest_configs: List of backtest configurations
            backtest_func: Backtest execution function

        Returns:
            List of backtest results
        """
        logger.info(
            f"Running {len(backtest_configs)} backtests in parallel "
            f"with {self.processor.max_workers} workers"
        )

        results = await self.processor.execute_parallel(
            backtest_func,
            backtest_configs
        )

        logger.info(f"Completed {len(results)} backtests")
        return results


# Utility functions for common parallel operations

async def parallel_indicator_calculation(
    symbols_data: Dict[str, List[float]],
    indicator_func: Callable,
    **kwargs
) -> Dict[str, Any]:
    """
    Calculate indicators for multiple symbols in parallel.

    Args:
        symbols_data: Dict mapping symbol -> price list
        indicator_func: Indicator calculation function
        **kwargs: Indicator parameters

    Returns:
        Dict mapping symbol -> indicator result
    """
    calculator = ParallelIndicatorCalculator()

    # Prepare datasets
    datasets = [
        {'symbol': symbol, 'prices': prices}
        for symbol, prices in symbols_data.items()
    ]

    # Calculate in parallel
    results = await calculator.calculate_indicators_batch(
        datasets,
        indicator_func,
        **kwargs
    )

    # Map back to symbols
    return {
        datasets[i]['symbol']: results[i]
        for i in range(len(datasets))
    }


async def parallel_map(
    func: Callable,
    items: List[Any],
    max_workers: Optional[int] = None
) -> List[Any]:
    """
    Convenience function to map a function over items in parallel.

    Args:
        func: Function to apply
        items: Items to process
        max_workers: Maximum worker processes

    Returns:
        List of results
    """
    processor = ParallelProcessor(max_workers)
    return await processor.map_parallel(func, items)


def _worker_calculate_macd_batch(prices_list: List[List[float]]) -> List[Tuple[float, float, float]]:
    """
    Worker function for batch MACD calculation (most CPU-intensive indicator).
    Must be at module level for pickling.

    Args:
        prices_list: List of price lists

    Returns:
        List of (macd_line, signal_line, histogram) tuples
    """
    from indicators import calculate_macd

    results = []
    for prices in prices_list:
        try:
            result = calculate_macd(prices)
            results.append(result)
        except Exception as e:
            # Return None for failed calculations
            results.append((None, None, None))

    return results


def _worker_calculate_rsi_batch(prices_list: List[List[float]], period: int = 14) -> List[float]:
    """
    Worker function for batch RSI calculation.
    Must be at module level for pickling.

    Args:
        prices_list: List of price lists
        period: RSI period

    Returns:
        List of RSI values
    """
    from indicators import calculate_rsi

    results = []
    for prices in prices_list:
        try:
            result = calculate_rsi(prices, period)
            results.append(result)
        except Exception as e:
            results.append(None)

    return results


def _worker_calculate_bollinger_batch(
    prices_list: List[List[float]],
    period: int = 20,
    std_dev: float = 2.0
) -> List[Tuple[float, float, float]]:
    """
    Worker function for batch Bollinger Bands calculation.
    Must be at module level for pickling.

    Args:
        prices_list: List of price lists
        period: MA period
        std_dev: Standard deviation multiplier

    Returns:
        List of (upper, middle, lower) tuples
    """
    from indicators import calculate_bollinger_bands

    results = []
    for prices in prices_list:
        try:
            result = calculate_bollinger_bands(prices, period, std_dev)
            results.append(result)
        except Exception as e:
            results.append((None, None, None))

    return results


# Benchmark utilities

async def benchmark_parallel_vs_sequential(
    func: Callable,
    data_items: List[Any],
    max_workers: Optional[int] = None
) -> Dict[str, Any]:
    """
    Benchmark parallel vs sequential execution.

    Args:
        func: Function to benchmark
        data_items: Data items to process
        max_workers: Number of parallel workers

    Returns:
        Dict with timing and speedup results
    """
    import time

    # Sequential execution
    logger.info("Running sequential benchmark...")
    start_seq = time.time()
    sequential_results = [func(item) for item in data_items]
    sequential_time = time.time() - start_seq

    # Parallel execution
    logger.info("Running parallel benchmark...")
    start_par = time.time()
    parallel_results = await parallel_map(func, data_items, max_workers)
    parallel_time = time.time() - start_par

    # Calculate speedup
    speedup = sequential_time / parallel_time if parallel_time > 0 else 0

    results = {
        'sequential_time': sequential_time,
        'parallel_time': parallel_time,
        'speedup': speedup,
        'items_processed': len(data_items),
        'workers_used': max_workers or MAX_WORKERS,
        'efficiency_pct': (speedup / (max_workers or MAX_WORKERS)) * 100
    }

    logger.info(
        f"Benchmark complete: Sequential={sequential_time:.2f}s, "
        f"Parallel={parallel_time:.2f}s, Speedup={speedup:.2f}x"
    )

    return results
