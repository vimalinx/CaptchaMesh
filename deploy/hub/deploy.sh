#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" != 2 ]]; then
  echo "usage: $0 <ssh-host> <public-hostname>" >&2
  exit 2
fi

deploy_host="$1"
public_host="$2"
if [[ "${deploy_host}" == -* || "${deploy_host}" =~ [[:space:]] ]]; then
  echo "invalid SSH host" >&2
  exit 2
fi
if [[ ! "${public_host}" =~ ^[A-Za-z0-9.-]+$ || "${public_host}" == .* || "${public_host}" == *. ]]; then
  echo "invalid public hostname" >&2
  exit 2
fi

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
service_root="${project_root}/deploy/hub"

echo "Pre-deploy health check:"
curl --fail --silent --show-error --max-time 10 "https://${public_host}/healthz" \
  || echo "No healthy public service detected; continuing with bootstrap deployment."

remote_stage="$(ssh -- "${deploy_host}" mktemp -d /tmp/captchamesh-deploy.XXXXXX)"
cleanup() {
  ssh -- "${deploy_host}" "rm -rf -- '${remote_stage}'" >/dev/null 2>&1 || true
}
trap cleanup EXIT

scp -q -- \
  "${project_root}/broker.py" \
  "${project_root}/broker_asgi.py" \
  "${project_root}/challenge_protocol.py" \
  "${project_root}/relay_protocol.py" \
  "${project_root}/twocaptcha_compat.py" \
  "${project_root}/requirements.txt" \
  "${project_root}/tests/fixtures/empty_registrations.json" \
  "${service_root}/captchamesh-hub.service" \
  "${deploy_host}:${remote_stage}/"

ssh -- "${deploy_host}" "bash -s -- '${remote_stage}' '${public_host}'" <<'REMOTE'
set -euo pipefail
remote_stage="$1"
public_host="$2"

if ! id -u captchamesh >/dev/null 2>&1; then
  sudo useradd --system --home-dir /var/lib/captchamesh-hub \
    --shell /usr/sbin/nologin captchamesh
fi
sudo install -d -o root -g root -m 0755 /opt/captchamesh-hub
sudo install -d -o captchamesh -g captchamesh -m 0700 /var/lib/captchamesh-hub
sudo install -d -o root -g captchamesh -m 0750 /etc/captchamesh-hub

for key_name in api node; do
  key_path="/etc/captchamesh-hub/${key_name}.key"
  if ! sudo test -s "${key_path}"; then
    openssl rand -hex 32 | sudo tee "${key_path}" >/dev/null
  fi
  sudo chown root:captchamesh "${key_path}"
  sudo chmod 0640 "${key_path}"
done

printf '%s\n' \
  "CAPTCHAMESH_ALLOWED_HOSTS=${public_host},127.0.0.1,localhost" \
  'CAPTCHAMESH_ALLOW_PUBLIC_PAIRING=1' \
  | sudo tee /etc/captchamesh-hub/hub.env >/dev/null
sudo chown root:captchamesh /etc/captchamesh-hub/hub.env
sudo chmod 0640 /etc/captchamesh-hub/hub.env

for source_name in broker.py broker_asgi.py challenge_protocol.py relay_protocol.py \
  twocaptcha_compat.py requirements.txt; do
  sudo install -o root -g root -m 0644 "${remote_stage}/${source_name}" \
    "/opt/captchamesh-hub/${source_name}"
done
sudo install -o root -g root -m 0644 "${remote_stage}/empty_registrations.json" \
  /opt/captchamesh-hub/registrations.json
sudo install -o root -g root -m 0644 "${remote_stage}/captchamesh-hub.service" \
  /etc/systemd/system/captchamesh-hub.service

if ! sudo test -x /opt/captchamesh-hub/.venv/bin/python; then
  sudo python3 -m venv /opt/captchamesh-hub/.venv
fi
sudo /opt/captchamesh-hub/.venv/bin/pip install --disable-pip-version-check -q \
  -r /opt/captchamesh-hub/requirements.txt
sudo chown -R captchamesh:captchamesh /var/lib/captchamesh-hub

sudo systemctl daemon-reload
sudo systemctl enable captchamesh-hub.service
sudo systemctl restart captchamesh-hub.service
for attempt in $(seq 1 20); do
  if curl --fail --silent http://127.0.0.1:8890/healthz >/dev/null; then
    break
  fi
  if [[ "${attempt}" == 20 ]]; then
    sudo systemctl status captchamesh-hub.service --no-pager -l
    exit 1
  fi
  sleep 0.5
done

sudo systemctl is-active --quiet captchamesh-hub.service
sudo stat -c '%a %U:%G %n' \
  /var/lib/captchamesh-hub \
  /etc/captchamesh-hub/api.key \
  /etc/captchamesh-hub/node.key
REMOTE

curl --fail --silent --show-error --max-time 10 "https://${public_host}/healthz" >/dev/null
echo "CaptchaMesh Hub deployment healthy: https://${public_host}/healthz"
