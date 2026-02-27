"""
Automated Scheduler for Data Archival

Runs archival jobs on a schedule (default: daily at 2 AM).
"""

import asyncio
from datetime import datetime, time as dtime
from loguru import logger
from data_archival import DataArchiver


class ArchivalScheduler:
    """Schedules and runs periodic archival jobs."""

    def __init__(
        self,
        archiver: DataArchiver = None,
        run_time: dtime = dtime(hour=2, minute=0),  # 2 AM default
        dry_run: bool = False
    ):
        """
        Initialize scheduler.

        Args:
            archiver: DataArchiver instance
            run_time: Time of day to run archival (default 2 AM)
            dry_run: If True, run in simulation mode
        """
        self.archiver = archiver or DataArchiver()
        self.run_time = run_time
        self.dry_run = dry_run
        self._running = False

    async def wait_until_next_run(self) -> None:
        """Calculate and wait until next scheduled run time."""
        now = datetime.now()
        target = datetime.combine(now.date(), self.run_time)

        # If target time has passed today, schedule for tomorrow
        if target <= now:
            from datetime import timedelta
            target += timedelta(days=1)

        wait_seconds = (target - now).total_seconds()
        logger.info(
            f"Next archival job scheduled for {target.isoformat()} "
            f"({wait_seconds / 3600:.1f} hours from now)"
        )

        await asyncio.sleep(wait_seconds)

    async def run_scheduled_job(self) -> None:
        """Run a single archival job."""
        logger.info("Executing scheduled archival job...")

        try:
            summary = await self.archiver.run_archival_job(dry_run=self.dry_run)
            logger.info(f"Archival job completed: {summary}")
        except Exception as e:
            logger.error(f"Archival job failed: {e}")

    async def start(self) -> None:
        """Start the scheduler loop."""
        self._running = True
        logger.info(
            f"Archival scheduler started (run time: {self.run_time}, "
            f"dry_run: {self.dry_run})"
        )

        while self._running:
            await self.wait_until_next_run()
            await self.run_scheduled_job()

    def stop(self) -> None:
        """Stop the scheduler."""
        self._running = False
        logger.info("Archival scheduler stopped")


# Convenience function for integration
async def start_archival_scheduler(dry_run: bool = False) -> ArchivalScheduler:
    """
    Start archival scheduler in background.

    Args:
        dry_run: If True, run in simulation mode

    Returns:
        ArchivalScheduler instance
    """
    scheduler = ArchivalScheduler(dry_run=dry_run)
    # Run in background
    asyncio.create_task(scheduler.start())
    return scheduler


if __name__ == "__main__":
    # Test scheduler
    async def test_scheduler():
        logger.info("Testing archival scheduler...")

        # Create scheduler that runs immediately for testing
        scheduler = ArchivalScheduler(
            run_time=datetime.now().time(),  # Run now
            dry_run=True  # Dry run mode
        )

        # Run one job
        await scheduler.run_scheduled_job()

        logger.info("Scheduler test complete")

    asyncio.run(test_scheduler())
