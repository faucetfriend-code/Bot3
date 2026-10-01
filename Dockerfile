# Bot 3 Trading System - Dockerfile
# Multi-stage build for production deployment

# Stage 1: Build dependencies
FROM python:3.11-slim AS builder

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for layer caching
COPY trading_bot_v2/requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Stage 2: Production image
FROM python:3.11-slim AS production

# Create non-root user
RUN groupadd --gid 10001 botuser && useradd --uid 10001 --gid 10001 --no-create-home botuser

WORKDIR /app

# Copy installed dependencies from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY trading_bot_v2/ ./trading_bot_v2/
COPY interface.html ./interface.html
COPY deploy/vps/sqlite_backup.py ./deploy/vps/sqlite_backup.py
COPY deploy/vps/image_smoke.py ./deploy/vps/image_smoke.py

# Create necessary directories
RUN mkdir -p /app/data /app/logs /app/backups \
    "/app/server logs reports" /app/trading_bot_v2/backtesting/data

# Set ownership
RUN chown -R botuser:botuser /app

# Switch to non-root user
USER botuser

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Expose API port
EXPOSE 8000

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Run the application
CMD ["python", "-m", "trading_bot_v2.api_server"]
