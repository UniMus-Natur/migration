FROM python:3.12-bookworm

# Install system dependencies
# Full image includes git, curl, build-essential, pkg-config
# We add database clients and headers specifically needed
RUN apt-get update && apt-get install -y \
    vim \
    mariadb-client \
    libaio1 \
    libnsl2 \
    default-libmysqlclient-dev \
    libldap-dev \
    libsasl2-dev \
    iputils-ping \
    socat \
    openssh-server \
    unzip \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir /var/run/sshd \
    && echo 'root:root' | chpasswd \
    && sed -i 's/#PermitRootLogin prohibit-password/PermitRootLogin yes/' /etc/ssh/sshd_config \
    && echo "PermitUserEnvironment yes" >> /etc/ssh/sshd_config \
    # SSH login fix. Otherwise user is kicked off after login
    && sed -i 's@session\s*required\s*pam_loginuid.so@session optional pam_loginuid.so@g' /etc/pam.d/sshd

# Install Oracle Instant Client for python-oracledb thick mode support.
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

# Copy requirements first to leverage cache
COPY requirements.txt /app/
COPY specify7/requirements.txt /app/specify7/

# Install dependencies:
# 1) Install Specify 7 pins from submodule
# 2) Install migration-layer deps and controlled overrides (e.g. jsonschema)
RUN pip install --no-cache-dir -r /app/specify7/requirements.txt && \
    pip install --no-cache-dir -r /app/requirements.txt

# Copy the rest of the application code
COPY . /app/

# Generate necessary build files for Specify 7
RUN echo "VERSION = 'v7'" > /app/specify7/specifyweb/settings/build_version.py && \
    echo "SECRET_KEY = 'dummy_secret_key_for_build_process'" > /app/specify7/specifyweb/settings/secret_key.py


# Default command is bash to let you explore
EXPOSE 22

# Default command starts sshd
CMD ["/usr/sbin/sshd", "-D"]
