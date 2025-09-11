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
    curl

WORKDIR /app

# Install Poetry (user install) and configure in-project virtualenv
ENV POETRY_VERSION=1.8.3
RUN curl -sSL https://install.python-poetry.org | python3 - && \
    ln -s /root/.local/bin/poetry /usr/local/bin/poetry
ENV PATH="/root/.local/bin:${PATH}"

# Copy poetry files first for better layer caching
COPY pyproject.toml poetry.lock ./

# Configure Poetry to create venv in project and install only main deps
RUN poetry config virtualenvs.in-project true && \
    poetry install --only=main --no-dev --no-interaction

# Copy application code
COPY src/ ./src/
COPY run.sh .

# Add s6 service definitions
COPY rootfs/ /