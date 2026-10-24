FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    PORT=8000 \
    PATH="/root/.local/bin:${PATH}"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    procps \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install agy CLI into container
RUN curl -fsSL https://antigravity.google/cli/install.sh | bash || true

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/workspace /app/inbox /app/data

EXPOSE 8000

CMD ["python", "main.py"]
