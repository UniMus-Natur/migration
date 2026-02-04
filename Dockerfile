FROM python:3.11-slim-bookworm

# Install system dependencies
# git: for pip (submodule refs) and general usage
# vim: for editing
# curl/wget: for tools
# mariadb-client: to inspect the local DB
# libaio1: often returned by oracledb if thick mode is needed (though we use thin by default)
# build-essential, pkg-config, default-libmysqlclient-dev: for building mysqlclient
RUN apt-get update && apt-get install -y \
    git \
    vim \
    curl \
    mariadb-client \
    libaio1 \
    build-essential \
    pkg-config \
    default-libmysqlclient-dev \
    libldap-dev \
    libsasl2-dev \
    iputils-ping \
    socat \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first to leverage cache
COPY requirements.txt /app/
COPY specify7/requirements.txt /app/specify7/

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . /app/

# Default command is bash to let you explore
CMD ["/bin/bash"]
