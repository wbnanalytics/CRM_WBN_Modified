#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/wbn-crm/current"
VENV_DIR="/opt/wbn-crm/venv"
DATA_DIR="/var/lib/wbn-crm"
BACKUP_DIR="/var/backups/wbn-crm"

echo "=== 1. Installing System Packages ==="
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  python3-venv python3-pip nginx certbot python3-certbot-nginx sqlite3 git curl

echo "=== 2. Creating User & Directories ==="
id -u wbncrm >/dev/null 2>&1 || useradd -r -s /bin/false wbncrm
install -d -o wbncrm -g www-data "$DATA_DIR" "$BACKUP_DIR"
chmod 770 "$DATA_DIR" "$BACKUP_DIR"

echo "=== 3. Setting Up Python Virtual Environment ==="
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install -q --upgrade pip
"$VENV_DIR/bin/pip" install -q -r "$APP_DIR/requirements.txt"

echo "=== 4. Checking Production Environment File ==="
if [ ! -f /etc/wbn-crm.env ]; then
  echo "WARNING: /etc/wbn-crm.env does not exist yet. Please create it before starting services."
else
  chown root:wbncrm /etc/wbn-crm.env
  chmod 640 /etc/wbn-crm.env
fi

echo "=== 5. Installing Systemd Services & Timers ==="
install -m 644 "$APP_DIR/deploy/wbn-crm.service" /etc/systemd/system/wbn-crm.service
install -m 644 "$APP_DIR/deploy/wbn-crm-shopify.service" /etc/systemd/system/wbn-crm-shopify.service
install -m 644 "$APP_DIR/deploy/wbn-crm-shopify.timer" /etc/systemd/system/wbn-crm-shopify.timer
install -m 644 "$APP_DIR/deploy/wbn-crm-recognition-daily.timer" /etc/systemd/system/wbn-crm-recognition-daily.timer
install -m 644 "$APP_DIR/deploy/wbn-crm-recognition@.service" /etc/systemd/system/wbn-crm-recognition@.service
install -m 644 "$APP_DIR/deploy/wbn-crm-backup.service" /etc/systemd/system/wbn-crm-backup.service
install -m 644 "$APP_DIR/deploy/wbn-crm-backup.timer" /etc/systemd/system/wbn-crm-backup.timer

systemctl daemon-reload

echo "=== 6. Configuring Nginx ==="
install -m 644 "$APP_DIR/deploy/nginx-crm.conf" /etc/nginx/sites-available/wbn-crm
ln -sfn /etc/nginx/sites-available/wbn-crm /etc/nginx/sites-enabled/wbn-crm
rm -f /etc/nginx/sites-enabled/default || true

nginx -t && systemctl reload nginx || true

echo "=== 7. Enabling Production Timers & Services ==="
systemctl enable --now wbn-crm-shopify.timer
systemctl enable --now wbn-crm-recognition-daily.timer
systemctl enable --now wbn-crm-backup.timer

if [ -f /etc/wbn-crm.env ]; then
  systemctl enable --now wbn-crm.service
  echo "wbn-crm.service started successfully."
fi

echo "=== Deployment Completed Successfully ==="
