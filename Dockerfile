FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    PORT=8000 \
    PATH="/usr/local/bin:/root/.local/bin:${PATH}"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    procps \
    ca-certificates \
    bash \
    && rm -rf /var/lib/apt/lists/*

# Install Google Antigravity CLI (agy) directly into /usr/local/bin
RUN curl -fsSL https://antigravity.google/cli/install.sh | bash && \
    cp /root/.local/bin/agy /usr/local/bin/agy && \
    chmod +x /usr/local/bin/agy && \
    /usr/local/bin/agy --version

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Workspace setup
RUN mkdir -p /app/workspace /app/inbox /app/data

EXPOSE 10000

CMD ["python", "main.py"]
