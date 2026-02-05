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
    openssh-server \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir /var/run/sshd \
    && echo 'root:root' | chpasswd \
    && sed -i 's/#PermitRootLogin prohibit-password/PermitRootLogin yes/' /etc/ssh/sshd_config \
    # SSH login fix. Otherwise user is kicked off after login
    && sed -i 's@session\s*required\s*pam_loginuid.so@session optional pam_loginuid.so@g' /etc/pam.d/sshd

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
EXPOSE 22

# Default command starts sshd and then bash (or whatever else you need)
CMD ["/usr/sbin/sshd", "-D"]
