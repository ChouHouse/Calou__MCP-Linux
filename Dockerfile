FROM ghcr.io/calou-code-platform/ccp-base-debian:main

LABEL maintainer="caloutw"
LABEL org.opencontainers.image.title="MCP-Linux"
LABEL org.opencontainers.image.version="1.0.0"
LABEL org.opencontainers.image.authors="calou code platform"
LABEL org.opencontainers.image.description="[MCP-Linux] Give an AI full control of a Linux box through MCP, exposed with cloudflared."

# 對外設定 (同 remosh 風格的小寫環境變數)
ENV token=""
ENV cloudflared=""
ENV port="8000"

USER root

ENV DEBIAN_FRONTEND=noninteractive

# 安裝必要項目與常用工具
RUN apt-get update -y && \
    apt-get install -y --no-install-recommends \
    tini \
    curl \
    wget \
    ca-certificates \
    gnupg \
    sudo \
    git \
    tmux \
    build-essential \
    python3 \
    python3-pip \
    python3-venv \
    openssh-client \
    procps \
    psmisc \
    lsof \
    strace \
    iproute2 \
    iputils-ping \
    dnsutils \
    net-tools \
    jq \
    zip \
    unzip \
    xz-utils \
    rsync \
    file \
    less \
    nano \
    vim-tiny && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# 安裝 cloudflared
RUN mkdir -p --mode=0755 /usr/share/keyrings && \
    curl -fsSL https://pkg.cloudflare.com/cloudflare-public-v2.gpg -o /usr/share/keyrings/cloudflare-public-v2.gpg && \
    echo 'deb [signed-by=/usr/share/keyrings/cloudflare-public-v2.gpg] https://pkg.cloudflare.com/cloudflared any main' > /etc/apt/sources.list.d/cloudflared.list && \
    apt-get update -y && \
    apt-get install -y cloudflared && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# 移除進入警告
RUN echo 'Defaults lecture="never"' >> /etc/sudoers

# MCP server 專用 venv (和 AI 自己使用的 python 分開，避免被 pip 弄壞)
COPY server/requirements.txt /opt/mcp-linux/requirements.txt
RUN python3 -m venv /opt/mcp-linux/venv && \
    /opt/mcp-linux/venv/bin/pip install --no-cache-dir -r /opt/mcp-linux/requirements.txt
COPY server/server.py /opt/mcp-linux/server.py

# 建立 root 的 workspace 與啟動目錄
RUN mkdir -p /root/workspace /root/startup /var/log/mcp-linux

# 建立工作區域
RUN mkdir /ccp
WORKDIR /ccp

COPY .bashrc ./
COPY .bash_profile ./

COPY cloudflared-supervisor.sh ./
RUN chmod +x cloudflared-supervisor.sh

COPY run.sh ./
RUN chmod +x run.sh

COPY title ./

EXPOSE 8000
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/ccp/run.sh"]
