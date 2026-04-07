FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app/signal-daemon

COPY signal-daemon/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY signal-daemon/ ./

CMD ["python", "telegram_forwarder.py"]
