"""
Process manager for controlling the trading bot server process.
Provides functionality to start, stop, and monitor the server process.
"""

import subprocess
import signal
import os
import time
import logging
from typing import Optional, Dict, Any
import psutil

logger = logging.getLogger(__name__)

class TradingBotProcessManager:
    """Manages the trading bot process lifecycle"""

    def __init__(self):
        self.bot_process: Optional[subprocess.Popen] = None
        self.bot_pid: Optional[int] = None
        self.start_time: Optional[float] = None

    def start_server(self) -> Dict[str, Any]:
        """Start the trading bot process"""
        try:
            if self.is_bot_running():
                return {
                    "success": False,
                    "error": "Trading bot is already running",
                    "pid": self.bot_pid
                }

            logger.info("Starting trading bot...")

            # Prepare environment
            env = os.environ.copy()
            env["PYTHONPATH"] = os.getcwd()

            # Start trading bot as subprocess
            self.bot_process = subprocess.Popen(
                ["python", "main.py"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                cwd=os.getcwd(),
                # Don't use shell=True for security
            )

            self.bot_pid = self.bot_process.pid
            self.start_time = time.time()

            # Wait for bot to start up
            time.sleep(3)

            if self.is_bot_running():
                logger.info(f"Trading bot started successfully with PID {self.bot_pid}")
                return {
                    "success": True,
                    "message": "Trading bot started successfully",
                    "pid": self.bot_pid
                }
            else:
                # Bot failed to start
                error_output = ""
                if self.bot_process.stderr:
                    try:
                        error_output = self.bot_process.stderr.read().decode('utf-8', errors='ignore')
                    except:
                        pass

                logger.error(f"Trading bot failed to start. Error: {error_output}")
                return {
                    "success": False,
                    "error": f"Trading bot failed to start: {error_output[:200]}"
                }

        except Exception as e:
            logger.error(f"Failed to start server: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    def stop_server(self) -> Dict[str, Any]:
        """Stop the trading bot process"""
        try:
            if not self.is_bot_running():
                return {
                    "success": False,
                    "error": "Trading bot is not running"
                }

            logger.info(f"Stopping trading bot with PID {self.bot_pid}...")

            # Send SIGTERM first for graceful shutdown
            if self.bot_process:
                self.bot_process.terminate()

                # Wait up to 10 seconds for graceful shutdown
                try:
                    self.bot_process.wait(timeout=10)
                    logger.info("Trading bot stopped gracefully")
                except subprocess.TimeoutExpired:
                    # Force kill if graceful shutdown fails
                    logger.warning("Graceful shutdown failed, force killing trading bot")
                    self.bot_process.kill()
                    self.bot_process.wait(timeout=5)
                    logger.info("Trading bot force-killed")

            # Clean up
            self.bot_process = None
            self.bot_pid = None
            self.start_time = None

            return {
                "success": True,
                "message": "Trading bot stopped successfully"
            }

        except Exception as e:
            logger.error(f"Failed to stop server: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    def is_bot_running(self) -> bool:
        """Check if trading bot process is running"""
        if self.bot_process and self.bot_process.poll() is None:
            return True

        # Double-check with psutil if available
        if self.bot_pid:
            try:
                if psutil.pid_exists(self.bot_pid):
                    process = psutil.Process(self.bot_pid)
                    # Check if it's actually our python process
                    if process.is_running():
                        cmdline = process.cmdline()
                        if len(cmdline) > 1 and 'python' in cmdline[0] and 'main.py' in ' '.join(cmdline):
                            return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        return False

    def get_server_status(self) -> Dict[str, Any]:
        """Get detailed trading bot status"""
        running = self.is_bot_running()

        status_info = {
            "running": running,
            "pid": self.bot_pid if running else None,
            "uptime": "N/A",
            "memory_usage": "N/A",
            "cpu_usage": "N/A"
        }

        if running and self.start_time:
            # Calculate uptime
            uptime_seconds = time.time() - self.start_time
            hours = int(uptime_seconds // 3600)
            minutes = int((uptime_seconds % 3600) // 60)
            seconds = int(uptime_seconds % 60)
            status_info["uptime"] = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

            # Get resource usage if psutil available
            try:
                if self.bot_pid:
                    process = psutil.Process(self.bot_pid)
                    memory_info = process.memory_info()
                    status_info["memory_usage"] = f"{memory_info.rss / 1024 / 1024:.1f} MB"
                    status_info["cpu_usage"] = f"{process.cpu_percent(interval=0.1):.1f}%"
            except:
                pass

        return status_info

    def get_server_logs(self, lines: int = 50) -> Dict[str, Any]:
        """Get recent trading bot logs from stdout/stderr"""
        logs = {
            "stdout": [],
            "stderr": [],
            "available": False
        }

        if not self.bot_process:
            return logs

        try:
            logs["available"] = True

            # Get stdout logs
            if self.bot_process.stdout:
                stdout_data = self.bot_process.stdout.read()
                if stdout_data:
                    stdout_lines = stdout_data.decode('utf-8', errors='ignore').split('\n')
                    logs["stdout"] = stdout_lines[-lines:] if len(stdout_lines) > lines else stdout_lines

            # Get stderr logs
            if self.bot_process.stderr:
                stderr_data = self.bot_process.stderr.read()
                if stderr_data:
                    stderr_lines = stderr_data.decode('utf-8', errors='ignore').split('\n')
                    logs["stderr"] = stderr_lines[-lines:] if len(stderr_lines) > lines else stderr_lines

        except Exception as e:
            logger.error(f"Failed to read trading bot logs: {e}")

        return logs

# Global instance
process_manager = TradingBotProcessManager()