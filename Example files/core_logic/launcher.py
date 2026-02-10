"""
Launcher service for trading bot interface.
Provides HTTP endpoints to start/stop the main API server from the HTML interface.
"""

import asyncio
import subprocess
import time
import logging
import os
import signal
import psutil
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Dict, Any
import uvicorn
import aiohttp
from aiohttp import ClientTimeout

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Trading Bot Launcher", version="1.0.0")

# Add CORS middleware to allow requests from HTML interface
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for local development
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add request logging middleware
@app.middleware("http")
async def log_requests(request, call_next):
    logger.info(f"📨 Incoming request: {request.method} {request.url}")
    start_time = time.time()

    try:
        response = await call_next(request)
        process_time = time.time() - start_time
        logger.info(f"📤 Response: {response.status_code} in {process_time:.3f}s")
        return response
    except Exception as e:
        logger.error(f"💥 Request failed: {e}")
        raise

@app.on_event("startup")
async def startup_event():
    logger.info("🚀 FastAPI app startup event triggered")
    logger.info(f"Process ID: {os.getpid()}")
    logger.info(f"Working directory: {os.getcwd()}")
    logger.info("Testing critical components...")

    # Test file system access
    try:
        with open("api_server.py", "r") as f:
            f.read(100)  # Just test we can read
        logger.info("✅ File system access OK")
    except Exception as e:
        logger.error(f"❌ File system access failed: {e}")

    # Test subprocess creation
    try:
        result = subprocess.run(["python", "--version"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            logger.info("✅ Subprocess creation OK")
        else:
            logger.warning(f"⚠️ Subprocess test returned code: {result.returncode}")
    except Exception as e:
        logger.error(f"❌ Subprocess creation failed: {e}")

    logger.info("✅ Startup event completed successfully")

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("🛑 FastAPI app shutdown event triggered")
    # Cleanup resources if needed

class ServerStatus(BaseModel):
    running: bool
    pid: Optional[int] = None
    port: int = 8000
    uptime: Optional[float] = None

class LauncherResponse(BaseModel):
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None

# Global state
server_process: Optional[subprocess.Popen] = None
server_start_time: Optional[float] = None

def is_server_running() -> bool:
    """Check if the API server is running."""
    if server_process and server_process.poll() is None:
        return True

    # Double-check with psutil
    try:
        if server_process and server_process.pid:
            if psutil.pid_exists(server_process.pid):
                process = psutil.Process(server_process.pid)
                # Check if it's our API server
                cmdline = process.cmdline()
                if len(cmdline) > 1 and 'python' in cmdline[0] and 'api_server.py' in ' '.join(cmdline):
                    return True
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass

    return False

async def wait_for_server_ready(timeout: int = 30) -> bool:
    """Wait for the API server to be ready by checking if it responds."""
    for _ in range(timeout):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get('http://localhost:8000/api/health', timeout=ClientTimeout(total=2)) as response:
                    if response.status == 200:
                        return True
        except Exception as e:
            logger.debug(f"Server health check failed: {e}")
            pass
        await asyncio.sleep(1)

    return False

@app.get("/status")
async def get_launcher_status() -> ServerStatus:
    """Get the current status of the launcher and API server."""
    try:
        logger.info("Status endpoint called")
        running = is_server_running()
        uptime = None

        if running and server_start_time:
            uptime = time.time() - server_start_time

        return ServerStatus(
            running=running,
            pid=server_process.pid if server_process else None,
            uptime=uptime
        )
    except Exception as e:
        logger.error(f"Status endpoint error: {e}")
        raise HTTPException(status_code=500, detail=f"Status check failed: {str(e)}")

@app.post("/start-server")
async def start_api_server() -> LauncherResponse:
    """Start the API server as a subprocess."""
    global server_process, server_start_time

    try:
        logger.info("Start server endpoint called")

        if is_server_running():
            logger.info("API server already running")
            return LauncherResponse(
                success=False,
                message="API server is already running",
                data={"pid": server_process.pid if server_process else None}
            )

        logger.info("Starting API server...")

        # Prepare environment
        env = os.environ.copy()
        env["PYTHONPATH"] = os.getcwd()

        # Start API server as subprocess
        server_process = subprocess.Popen(
            ["python", "api_server.py"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=os.getcwd(),
            # Don't use shell=True for security
        )

        server_start_time = time.time()
        logger.info(f"API server process started with PID {server_process.pid}")

        # Wait for server to be ready
        server_ready = await wait_for_server_ready()

        if server_ready:
            logger.info("API server started successfully")
            return LauncherResponse(
                success=True,
                message="API server started successfully",
                data={
                    "pid": server_process.pid,
                    "port": 8000
                }
            )
        else:
            # Server process started but not responding
            logger.error("API server process started but not responding")
            return LauncherResponse(
                success=False,
                message="API server process started but failed to respond",
                data={"pid": server_process.pid}
            )

    except Exception as e:
        logger.error(f"Failed to start API server: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return LauncherResponse(
            success=False,
            message=f"Failed to start API server: {str(e)}"
        )

@app.post("/stop-server")
async def stop_api_server() -> LauncherResponse:
    """Stop the API server subprocess."""
    global server_process, server_start_time

    if not is_server_running():
        return LauncherResponse(
            success=False,
            message="API server is not running"
        )

    try:
        logger.info(f"Stopping API server with PID {server_process.pid}")

        # Send SIGTERM first for graceful shutdown
        if server_process:
            server_process.terminate()

            # Wait up to 10 seconds for graceful shutdown
            try:
                server_process.wait(timeout=10)
                logger.info("API server stopped gracefully")
            except subprocess.TimeoutExpired:
                logger.warning("Graceful shutdown failed, force killing API server")
                server_process.kill()
                server_process.wait(timeout=5)
                logger.info("API server force-killed")

        # Clean up
        server_process = None
        server_start_time = None

        return LauncherResponse(
            success=True,
            message="API server stopped successfully"
        )

    except Exception as e:
        logger.error(f"Failed to stop API server: {e}")
        return LauncherResponse(
            success=False,
            message=f"Failed to stop API server: {str(e)}"
        )

@app.get("/health")
async def health_check():
    """Enhanced health check with diagnostics."""
    logger.info("🏥 Health check called")

    health_data = {
        "status": "healthy",
        "service": "launcher",
        "timestamp": time.time(),
        "diagnostics": {
            "process_id": os.getpid(),
            "server_process": server_process.pid if server_process else None,
            "uptime": time.time() - server_start_time if server_start_time else None,
            "working_directory": os.getcwd()
        }
    }

    logger.info(f"🏥 Health check response: {health_data}")
    return health_data

@app.get("/debug")
async def debug_info():
    """Debug information endpoint."""
    import platform
    import sys

    logger.info("🔍 Debug info requested")

    debug_data = {
        "platform": platform.platform(),
        "python_version": sys.version,
        "working_directory": os.getcwd(),
        "process_id": os.getpid(),
        "server_process": server_process.pid if server_process else None,
        "environment": {
            "PYTHONPATH": os.environ.get("PYTHONPATH"),
            "PATH": os.environ.get("PATH", "")[:200] + "..." if len(os.environ.get("PATH", "")) > 200 else os.environ.get("PATH")
        },
        "files_exist": {
            "api_server.py": os.path.exists("api_server.py"),
            "main.py": os.path.exists("main.py"),
            "launcher.py": os.path.exists("launcher.py")
        },
        "imports": {
            "fastapi": "OK",
            "uvicorn": "OK",
            "aiohttp": "OK",
            "psutil": "OK"
        }
    }

    logger.info(f"🔍 Debug info: {debug_data}")
    return debug_data

if __name__ == "__main__":
    try:
        logger.info("Starting Trading Bot Launcher service on port 8765...")
        logger.info("Testing imports...")

        # Test critical imports
        import fastapi
        import uvicorn
        import aiohttp
        import psutil
        logger.info("All imports successful")

        uvicorn.run(
            "launcher:app",
            host="127.0.0.1",  # Only listen on localhost for security
            port=8765,
            reload=False,
            log_level="info"
        )
    except Exception as e:
        logger.critical(f"Launcher failed to start: {e}")
        logger.critical(f"Error type: {type(e).__name__}")
        import traceback
        logger.critical(f"Traceback: {traceback.format_exc()}")
        raise