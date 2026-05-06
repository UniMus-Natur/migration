FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y \
    curl \
    unzip \
    libaio1 \
    libnsl2 \
    && rm -rf /var/lib/apt/lists/*

# Oracle Instant Client for python-oracledb thick mode
ARG ORACLE_IC_URL="https://download.oracle.com/otn_software/linux/instantclient/2113000/instantclient-basiclite-linux.x64-21.13.0.0.0dbru.zip"
RUN mkdir -p /opt/oracle && \
    curl -fsSL "${ORACLE_IC_URL}" -o /tmp/instantclient.zip && \
    unzip -q /tmp/instantclient.zip -d /opt/oracle && \
    rm -f /tmp/instantclient.zip && \
    ln -s "$(ls -d /opt/oracle/instantclient_* | awk 'NR==1{print;exit}')" /opt/oracle/instantclient && \
    echo "/opt/oracle/instantclient" > /etc/ld.so.conf.d/oracle-instantclient.conf && \
    ldconfig

ENV ORACLE_USE_THICK_MODE=true
ENV ORACLE_CLIENT_LIB_DIR=/opt/oracle/instantclient
ENV LD_LIBRARY_PATH=/opt/oracle/instantclient

WORKDIR /app

COPY services/migration-harness/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY . /app

EXPOSE 8088

CMD ["python", "services/migration-harness/app.py"]
