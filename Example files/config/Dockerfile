# Multi-stage Docker build for Trading Bot
FROM python:3.11-slim as base

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app
ENV DATABASE_PATH=/app/data/trading_bot.db

# Agent Lightning environment variables
ENV AGL_ENABLE_TRAINING=true
ENV AGL_TRAINING_MODE=error_recovery
ENV AGL_TRACES_DIR=/app/data/agl_traces

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    libsqlite3-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd --create-home --shell /bin/bash app \
    && mkdir -p /app/data /app/logs /app/backups \
    && chown -R app:app /app

# Set work directory
WORKDIR /app

# Install Python dependencies
FROM base as dependencies

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Production stage
FROM dependencies as production

# Copy application code
COPY --chown=app:app . .

# Create data directories with proper permissions
RUN mkdir -p /app/data /app/logs /app/backups /app/data/agl_traces \
    && chown -R app:app /app/data /app/logs /app/backups

# Switch to non-root user
USER app

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

# Expose port
EXPOSE 8000

# Default command - run API server on port 8000
CMD ["uvicorn", "api_server:app", "--host", "0.0.0.0", "--port", "8000"]