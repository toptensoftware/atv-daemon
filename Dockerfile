FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ATVD_STORAGE=/data/pyatv.conf

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY config.py atvdaemon.py atvbench.py ./

VOLUME /data
CMD ["python", "atvdaemon.py"]
