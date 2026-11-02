#!/usr/bin/env bash
set -euo pipefail

domain=crm.wellbeingnutrition.com
expected_ip=74.225.198.122

if test -f "/etc/letsencrypt/live/$domain/fullchain.pem"; then
  exit 0
fi

resolved_ip="$({ getent ahostsv4 "$domain" || true; } | awk 'NR == 1 {print $1}')"
if test "$resolved_ip" != "$expected_ip"; then
  exit 0
fi

certbot --nginx --domain "$domain" --non-interactive --agree-tos \
  --register-unsafely-without-email --redirect
