# 更換 cloudflared 通道 token
cfd() {
    if [ -z "$1" ]; then
        echo "If you want change your cloudflared tunnel token, please use 'cfd <YOUR_TOKEN>'."
        return 1
    fi

    CLOUDFLARED_FLAGS="/ccp/flags/cloudflared"
    mkdir -p "$(dirname "$CLOUDFLARED_FLAGS")"
    echo "$1" > "$CLOUDFLARED_FLAGS"
    echo "Token has been changed."

    # supervisor 會在 5 秒內用新 token 重新啟動 cloudflared
    pkill -x cloudflared 2>/dev/null
    pgrep -f cloudflared-supervisor.sh > /dev/null || (nohup /ccp/cloudflared-supervisor.sh > /dev/null 2>&1 &)
}

# 顯示或重新產生 MCP 存取 token (立即生效，不需重啟)
mcptoken() {
    local f=/ccp/flags/token
    if [ "$1" = "new" ]; then
        head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > "$f"
        chmod 600 "$f"
        echo "New token:"
    elif [ -n "$1" ]; then
        printf '%s' "$1" > "$f"
        chmod 600 "$f"
        echo "Token set:"
    fi
    cat "$f"; echo
}

cd /root/workspace 2>/dev/null
