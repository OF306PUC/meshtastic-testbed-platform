FROM python:3.12-slim

WORKDIR /app

# Build context is the repo root, not monitor/, so that src/common can be copied
# in alongside the app. The dashboard reads the surveyed node positions through
# common.mesh_config rather than parsing mesh_config.json itself: nothing
# provisions that block onto a radio, so the monitor is its only consumer and
# therefore the only place its validation can run.
COPY monitor/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY monitor/ ./
# mesh_config.py imports nothing but json, so the package travels alone.
COPY src/common/ ./common/

ENV PYTHONUNBUFFERED=1

CMD ["python", "app.py"]
