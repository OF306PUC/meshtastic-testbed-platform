FROM python:3.12-slim

WORKDIR /app

# Install the slim gateway dependency set (see src/gateway/requirements.txt).
# Build context is the repo root so the whole src/ package can be copied.
COPY src/gateway/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the src/ package so `gateway` and its imports resolve. receiver.py adds
# /app/src to sys.path at import time, so `from gateway import ...` works.
COPY src/ ./src/

ENV PYTHONUNBUFFERED=1

# No --port here: the serial port comes from GATEWAY_SERIAL_PORT (set in
# docker-compose.yaml). mesh_config.json is mounted at /app/mesh_config.json.
CMD ["python", "src/gateway/receiver.py"]
