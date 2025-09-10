ARG BUILD_FROM
FROM ${BUILD_FROM}

# Install Python and required system packages
RUN apk add --no-cache \
    python3 \
    py3-pip \
    python3-dev \
    gcc \
    musl-dev \
    bash \
    bashio

# Install Poetry
RUN pip3 install --no-cache-dir poetry

# Set up Python environment
WORKDIR /app

# Copy poetry files
COPY pyproject.toml poetry.lock ./

# Configure poetry and install dependencies
RUN poetry config virtualenvs.create false && \
    poetry install --only=main --no-dev

# Copy application code
COPY src/ ./src/
COPY run.sh .

# Set the startup command
CMD ["./run.sh"]