#!/bin/sh
# 依 TRUSTED_EDGE_CIDRS 產生 nginx 的 set_real_ip_from 清單（INFRA-033）。
#
# 放進 nginx 官方映像的 /docker-entrypoint.d/，容器啟動時執行；exit 非 0 時容器不啟動。
# TRUSTED_EDGE_CIDRS：以空白分隔的 CIDR 清單（Railway edge 的來源範圍，預設值寫在 Dockerfile）。
# 變數為空、或任一項不是 IPv4 / IPv6 CIDR 時 exit 1，不寫出任何檔案。
# REAL_IP_CONF：輸出路徑（預設 /etc/nginx/real-ip.conf；測試時覆寫）。

set -eu

out="${REAL_IP_CONF:-/etc/nginx/real-ip.conf}"
cidrs="${TRUSTED_EDGE_CIDRS:-}"

if [ -z "$(printf '%s' "$cidrs" | tr -d ' \t\n')" ]; then
    echo "40-real-ip.sh: TRUSTED_EDGE_CIDRS 未設定或為空" >&2
    exit 1
fi

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

for cidr in $cidrs; do
    if printf '%s\n' "$cidr" | grep -Eq '^([0-9]{1,3}\.){3}[0-9]{1,3}/[0-9]{1,2}$|^[0-9A-Fa-f:]*:[0-9A-Fa-f:]*/[0-9]{1,3}$'; then
        printf 'set_real_ip_from %s;\n' "$cidr" >> "$tmp"
    else
        echo "40-real-ip.sh: TRUSTED_EDGE_CIDRS 含非 CIDR 項目：${cidr}" >&2
        exit 1
    fi
done

cat "$tmp" > "$out"
