# Railway builds from repo root by default. Application code is in bot/.
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git libcurl4 \
    && rm -rf /var/lib/apt/lists/*

COPY bot/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY bot/ .

ENV PYTHONUNBUFFERED=1

CMD ["python3", "main.py"]
