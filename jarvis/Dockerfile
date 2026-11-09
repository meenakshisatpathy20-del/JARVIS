FROM python:3.12-slim
WORKDIR /app
COPY jarvis/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY config.yaml /app/config.yaml
COPY jarvis/ /app/jarvis/
ENV PYTHONUNBUFFERED=1
CMD ["uvicorn", "jarvis.app:app", "--host", "0.0.0.0", "--port", "8000"]
