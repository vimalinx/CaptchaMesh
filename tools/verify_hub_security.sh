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
project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ssh_hostname="$(ssh -G "${deploy_host}" 2>/dev/null \
  | awk '$1 == "hostname" {print $2; exit}')"
origin_host="$(getent ahostsv4 "${ssh_hostname}" 2>/dev/null \
  | awk '$2 == "STREAM" {print $1; exit}' || true)"

if [[ -z "${origin_host}" ]]; then
  echo "unable to resolve deploy host to an IPv4 address" >&2
  exit 1
fi

if (
  cd "${project_root}"
  git ls-files --cached --others --exclude-standard -z \
    | xargs -0 grep -a -F -q -- "${origin_host}"
); then
  echo "origin address remains in the working tree" >&2
  exit 1
fi

while read -r object_id object_type; do
  if [[ "${object_type}" == "blob" ]] \
    && git -C "${project_root}" cat-file blob "${object_id}" | grep -a -F -q "${origin_host}"; then
    echo "origin address remains in a Git object" >&2
    exit 1
  fi
done < <(git -C "${project_root}" cat-file --batch-all-objects \
  --batch-check='%(objectname) %(objecttype)')

public_addresses="$(ssh -o BatchMode=yes "${deploy_host}" \
  "dig +short A '${public_host}' @1.1.1.1; dig +short AAAA '${public_host}' @1.1.1.1")"
if grep -F -x -q "${origin_host}" <<<"${public_addresses}"; then
  echo "public DNS still resolves to the origin" >&2
  exit 1
fi

response_headers="$(curl -fsSI --connect-timeout 5 --max-time 10 \
  "https://${public_host}/healthz")"
grep -qi '^HTTP/2 200' <<<"${response_headers}"
grep -qi '^cache-control: no-store' <<<"${response_headers}"
grep -qi '^x-content-type-options: nosniff' <<<"${response_headers}"
grep -qi '^x-frame-options: DENY' <<<"${response_headers}"
grep -qi '^strict-transport-security: max-age=31536000' <<<"${response_headers}"

http_response_headers="$(curl -fsSI --connect-timeout 5 --max-time 10 \
  "http://${public_host}/healthz")"
grep -qi '^HTTP/1.1 308' <<<"${http_response_headers}"
grep -qi "^location: https://${public_host}/healthz" <<<"${http_response_headers}"

origin_body="$(curl -sS --connect-timeout 5 --max-time 10 \
  --resolve "${public_host}:80:${origin_host}" "http://${public_host}/healthz" 2>/dev/null || true)"
if grep -q '"protocolVersion":"3"' <<<"${origin_body}"; then
  echo "origin HTTP still exposes the Hub" >&2
  exit 1
fi

ssh -o BatchMode=yes "${deploy_host}" bash -s -- "${public_host}" "${origin_host}" <<'REMOTE'
set -euo pipefail
public_host="$1"
origin_host="$2"

[[ "$(curl -sS -o /dev/null -w '%{http_code}' \
  -H "Host: ${public_host}" http://127.0.0.1:8890/healthz)" == 200 ]]
[[ "$(curl -sS -o /dev/null -w '%{http_code}' \
  -H "Host: ${public_host}:443@evil.example" http://127.0.0.1:8890/healthz)" == 400 ]]
[[ "$(curl -sS -o /dev/null -w '%{http_code}' \
  -H "Host: ${public_host}" -H 'CF-Connecting-IP: bad,header' \
  http://127.0.0.1:8890/healthz)" == 400 ]]
[[ "$(curl -sS -o /dev/null -w '%{http_code}' \
  -H "Host: ${public_host}" http://127.0.0.1:8890/v1/registrations)" == 401 ]]

sudo ss -ltnp | grep -q '127.0.0.1:8890'
! sudo ss -ltnp | grep -Eq '(0.0.0.0|\[::\]):8890'
! sudo nginx -T 2>/dev/null | grep -q "server_name ${public_host}"
! sudo grep -R -F -q -- "${origin_host}" \
  /opt/captchamesh-hub /etc/systemd/system/captchamesh-hub.service \
  /etc/nginx/sites-available /etc/nginx/sites-enabled

[[ "$(systemctl show captchamesh-hub -p MemoryMax --value)" == 536870912 ]]
[[ "$(systemctl show captchamesh-hub -p TasksMax --value)" == 64 ]]
[[ "$(systemctl show captchamesh-hub -p LimitNOFILE --value)" == 4096 ]]
ip_deny="$(systemctl show captchamesh-hub -p IPAddressDeny --value)"
[[ "${ip_deny}" == *'0.0.0.0/0'* && "${ip_deny}" == *'::/0'* ]]

/opt/captchamesh-hub/.venv/bin/python - <<'PY'
import cryptography
assert cryptography.__version__ == "50.0.0"
PY

systemctl is-active --quiet captchamesh-hub cloudflared
[[ "$(systemctl show captchamesh-hub -p User --value)" == captchamesh ]]
[[ "$(systemctl show captchamesh-hub -p Group --value)" == captchamesh ]]
[[ "$(systemctl show cloudflared -p User --value)" == cloudflared ]]
[[ "$(systemctl show cloudflared -p Group --value)" == cloudflared ]]
[[ "$(systemctl show cloudflared -p NoNewPrivileges --value)" == yes ]]
[[ "$(systemctl show cloudflared -p ProtectSystem --value)" == strict ]]
[[ "$(sudo stat -c '%a %U:%G' /etc/cloudflared/tunnel.token)" == \
  '640 root:cloudflared' ]]
REMOTE

echo "CaptchaMesh security verification passed"
