FROM python:3.11-bookworm

# Install system dependencies
# Full image includes git, curl, build-essential, pkg-config
# We add database clients and headers specifically needed
RUN apt-get update && apt-get install -y \
    vim \
    mariadb-client \
    libaio1 \
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

# Generate necessary build files for Specify 7
RUN echo "VERSION = 'v7'" > /app/specify7/specifyweb/settings/build_version.py && \
    echo "SECRET_KEY = 'dummy_secret_key_for_build_process'" > /app/specify7/specifyweb/settings/secret_key.py


# Default command is bash to let you explore
CMD ["/bin/bash"]
