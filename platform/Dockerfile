FROM python:3.12-slim
ARG SERVICE
WORKDIR /app
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY common/telemetry.py /app/telemetry.py
COPY ${SERVICE}/ /app/
ENV PYTHONUNBUFFERED=1
