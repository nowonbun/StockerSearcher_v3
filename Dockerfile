FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        chromium chromium-driver curl git libasound2 libgbm1 libgtk-3-0 \
        libnss3 libxss1 libu2f-udev fonts-liberation \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/stocksearcher
COPY requirements-stocksearcher.txt /requirements-stocksearcher.txt
RUN pip install --no-cache-dir -r /requirements-stocksearcher.txt

COPY src /opt/stocksearcher
