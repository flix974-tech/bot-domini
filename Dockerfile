FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot/ ./bot/

# Il DB vive in /app/data (monta un volume per persisterlo)
VOLUME ["/app/data"]

CMD ["python", "-m", "bot.main"]
