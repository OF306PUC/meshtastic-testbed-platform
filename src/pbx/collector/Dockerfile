FROM python:3.12-slim

WORKDIR /app

# Build context is the repo root so the whole src/ package can be copied.
COPY src/pbx/collector/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# src/gateway is copied too: the collector reuses MQTTConnector from there. Its
# heavier requirements are NOT installed — only the modules that are imported
# need to resolve, and mqtt_connector needs nothing beyond paho.
COPY src/ ./src/

ENV PYTHONUNBUFFERED=1

# No --port here: the console device comes from NODE_SERIAL_PORT.
CMD ["python", "src/pbx/collector/node_logd.py"]
