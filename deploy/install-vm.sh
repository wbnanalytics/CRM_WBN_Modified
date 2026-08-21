#!/usr/bin/env bash
set -euo pipefail

APP_DIR=/opt/wbn-crm/current
VENV_DIR=/opt/wbn-crm/venv

if ! command -v certbot >/dev/null 2>&1; then
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq certbot python3-certbot-nginx
fi

install -d -o wbncrm -g www-data /var/lib/wbn-crm /var/backups/wbn-crm
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install -q --upgrade pip
"$VENV_DIR/bin/pip" install -q -r "$APP_DIR/requirements.txt"

token="$({ curl -fsS -H Metadata:true \
  'http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https%3A%2F%2Fvault.azure.net'; } | \
  python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')"

fetch_secret() {
  curl -fsS -H "Authorization: Bearer $token" \
    "https://wbn-crm-kv-350869.vault.azure.net/secrets/$1?api-version=7.4" | \
    python3 -c 'import json,sys; print(json.load(sys.stdin)["value"])'
}

curl -fsS -H "Authorization: Bearer $token" \
  'https://wbn-crm-kv-350869.vault.azure.net/secrets/production-env-base?api-version=7.4' | \
  python3 -c 'import json,sys; open("/etc/wbn-crm.env", "w").write(json.load(sys.stdin)["value"].rstrip() + "\n")'

printf '%s\n' \
  "SECRET_KEY=$(fetch_secret flask-secret-key)" \
  "AZURE_CLIENT_ID=$(fetch_secret azure-client-id)" \
  "AZURE_CLIENT_SECRET=$(fetch_secret azure-client-secret)" \
  "AZURE_TENANT_ID=$(fetch_secret azure-tenant-id)" \
  'BIK_REPORT_EMAIL=implementations@wellbeingnutrition.com' \
  'CRM_DATA_DIR=/var/lib/wbn-crm' \
  'APP_ENV=production' \
  'AZURE_REDIRECT_URI=https://crm.wellbeingnutrition.com/auth/microsoft/callback' \
  >> /etc/wbn-crm.env
chown root:wbncrm /etc/wbn-crm.env
chmod 640 /etc/wbn-crm.env

install -m 644 "$APP_DIR/deploy/wbn-crm.service" /etc/systemd/system/wbn-crm.service
install -m 644 "$APP_DIR/deploy/wbn-crm-sync.service" /etc/systemd/system/wbn-crm-sync.service
install -m 644 "$APP_DIR/deploy/wbn-crm-sync.timer" /etc/systemd/system/wbn-crm-sync.timer
install -m 644 "$APP_DIR/deploy/wbn-crm-today.service" /etc/systemd/system/wbn-crm-today.service
install -m 644 "$APP_DIR/deploy/wbn-crm-today.timer" /etc/systemd/system/wbn-crm-today.timer
install -m 644 "$APP_DIR/deploy/wbn-crm-backup.service" /etc/systemd/system/wbn-crm-backup.service
install -m 644 "$APP_DIR/deploy/wbn-crm-backup.timer" /etc/systemd/system/wbn-crm-backup.timer
install -m 644 "$APP_DIR/deploy/wbn-crm-cert.service" /etc/systemd/system/wbn-crm-cert.service
install -m 644 "$APP_DIR/deploy/wbn-crm-cert.timer" /etc/systemd/system/wbn-crm-cert.timer
install -m 644 "$APP_DIR/deploy/nginx-crm.conf" /etc/nginx/sites-available/wbn-crm
ln -sfn /etc/nginx/sites-available/wbn-crm /etc/nginx/sites-enabled/wbn-crm
nginx -t
systemctl reload nginx

systemctl daemon-reload
systemctl enable --now wbn-crm.service wbn-crm-sync.timer wbn-crm-today.timer wbn-crm-backup.timer wbn-crm-cert.timer
