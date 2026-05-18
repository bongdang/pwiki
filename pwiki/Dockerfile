FROM python:3.13-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && git config --system --add safe.directory /data/git \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies first so Docker can reuse the layer cache.
COPY requirements.lock.txt .
RUN pip install --no-cache-dir -r requirements.lock.txt

# Copy source.
COPY . .

EXPOSE 5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "60", "app:app"]
