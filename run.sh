#!/bin/bash
cat /ccp/title
echo ""

cd /ccp

# 創建旗標
mkdir -p flags

TOKEN_FILE="flags/token"
CLOUDFLARED_FLAGS="flags/cloudflared"
INIT_FLAGS="flags/init"

# 初始化 root 的 shell 設定
if [ ! -f "$INIT_FLAGS" ]; then
    echo "Initialization..."
    cp /ccp/.bashrc /root/.bashrc
    cp /ccp/.bash_profile /root/.bash_profile
    touch "$INIT_FLAGS"
fi

# 判斷 token：環境變數優先，其次是已存在的旗標，最後自動產生
if [ -n "$token" ]; then
    printf '%s' "$token" > "$TOKEN_FILE"
elif [ ! -s "$TOKEN_FILE" ]; then
    echo "No token provided, generating a random one..."
    head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > "$TOKEN_FILE"
fi
chmod 600 "$TOKEN_FILE"

# 判斷是否有 Cloudflared Env
if [ -n "$cloudflared" ]; then
    echo "Initialization cloudflared..."
    echo "$cloudflared" > "$CLOUDFLARED_FLAGS"
fi

# 判斷 Cloudflared 旗標是否存在
if [ -s "$CLOUDFLARED_FLAGS" ]; then
    echo "Starting cloudflared..."
    nohup /ccp/cloudflared-supervisor.sh > /dev/null 2>&1 &
else
    echo "No cloudflared token, MCP is only reachable on port ${port:-8000}."
fi

# 自啟動目錄
STARTUP_DIR="/root/startup"
mkdir -p "$STARTUP_DIR"

# 自啟動
shopt -s nullglob
for file in "$STARTUP_DIR"/*.sh; do
    [ -f "$file" ] || continue
    base="$(basename "$file" .sh)"
    sanitized="${base//./_}"
    session="${sanitized}__startup"
    chmod +x "$file"
    tmux new-session -d -s "$session" "$file"
done

export MCP_PORT="${port:-8000}"
export MCP_TOKEN_FILE="/ccp/flags/token"
# token 由檔案提供，避免環境變數蓋掉 `mcptoken new` 的結果
unset MCP_TOKEN token

echo ""
echo "================ MCP-Linux ================"
echo " Endpoint : http://<host>:${MCP_PORT}/mcp"
echo " Token    : $(cat "$TOKEN_FILE")"
echo " Header   : Authorization: Bearer <token>   (or ?token=<token>)"
echo "==========================================="
echo "Server opened."
exec /opt/mcp-linux/venv/bin/python /opt/mcp-linux/server.py
