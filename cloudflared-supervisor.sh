#!/bin/bash
# 持續運行 cloudflared；每輪重新讀取 token，所以 `cfd <token>` 只需要 pkill 就會換新通道。
FLAG="/ccp/flags/cloudflared"
while true; do
    if [ -s "$FLAG" ]; then
        cloudflared_token=$(tr -d '\n\r ' < "$FLAG")
        cloudflared tunnel --no-autoupdate run --token "$cloudflared_token" >> /var/log/cloudflared.log 2>&1
    fi
    sleep 5
done
