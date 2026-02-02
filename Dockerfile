FROM python:3.12-slim

WORKDIR /app

# Install runtime deps
RUN pip install --no-cache-dir flask requests

# Copy project
COPY mcp_server.py ./mcp_server.py
COPY scripts/ ./scripts/
COPY config.json ./config.json

ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1 PYTHONIOENCODING=utf-8

EXPOSE 5678

CMD ["python", "mcp_server.py"]