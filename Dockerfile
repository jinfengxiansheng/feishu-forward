FROM python:3.13-slim

# Install Node.js 22
RUN apt-get update && \
    apt-get install -y curl gnupg && \
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y nodejs && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install lark-cli globally
RUN npm install -g @larksuite/cli

# Set HOME to /data for persistent config/tokens (Railway Volume)
ENV HOME=/data
ENV DATA_DIR=/data

# Create app directory
WORKDIR /app

# Copy script
COPY feishu-forward-railway.py .

# Create data directory and lark-cli config dir
RUN mkdir -p /data/.lark-cli/cache /data/.lark-cli/logs

CMD ["python", "feishu-forward-railway.py"]
