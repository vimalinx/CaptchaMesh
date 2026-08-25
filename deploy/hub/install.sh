#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "${script_dir}/../.." && pwd)"
domain=""
tunnel_token_file=""
backup_public_key=""
public_pairing=1
non_interactive=0
skip_public_check=0
test_root="${CAPTCHAMESH_TEST_ROOT:-}"
temporary_files=()

cleanup() {
  local path
  for path in "${temporary_files[@]}"; do
    rm -f -- "${path}"
  done
}
trap cleanup EXIT

usage() {
  cat <<'EOF'
usage: sudo ./deploy/hub/install.sh [options]

Options:
  --domain HOSTNAME            Public Hub hostname, for example mesh.example.com
  --tunnel-token-file PATH     Cloudflare Tunnel token file; never pass the token itself
  --backup-public-key PATH     Optional GPG public key for encrypted daily backups
  --private-pairing            Require an API key for pairing instead of public one-time pairing
  --non-interactive            Do not prompt; --domain is required
  --skip-public-check          Skip the final HTTPS check
  -h, --help                   Show this help

Run the same command again to upgrade. Existing keys, database and tunnel token are preserved
unless a replacement token file is explicitly supplied.
EOF
}

while (($#)); do
  case "$1" in
    --domain)
      [[ $# -ge 2 ]] || { echo "--domain requires a value" >&2; exit 2; }
      domain="$2"
      shift 2
      ;;
    --tunnel-token-file)
      [[ $# -ge 2 ]] || { echo "--tunnel-token-file requires a value" >&2; exit 2; }
      tunnel_token_file="$2"
      shift 2
      ;;
    --backup-public-key)
      [[ $# -ge 2 ]] || { echo "--backup-public-key requires a value" >&2; exit 2; }
      backup_public_key="$2"
      shift 2
      ;;
    --private-pairing)
      public_pairing=0
      shift
      ;;
    --non-interactive)
      non_interactive=1
      shift
      ;;
    --skip-public-check)
      skip_public_check=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "${domain}" && "${non_interactive}" == 0 ]]; then
  read -r -p "Public Hub hostname (for example mesh.example.com): " domain
fi
valid_hostname=1
if [[ ${#domain} -gt 253 || "${domain}" != *.* ]]; then
  valid_hostname=0
else
  IFS=. read -r -a domain_labels <<<"${domain}"
  for label in "${domain_labels[@]}"; do
    if [[ ! "${label}" =~ ^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$ ]]; then
      valid_hostname=0
      break
    fi
  done
fi
if [[ "${valid_hostname}" == 0 ]]; then
  echo "invalid public hostname" >&2
  exit 2
fi
if [[ "${non_interactive}" == 0 && -z "${tunnel_token_file}" ]]; then
  tunnel_token=""
  read -r -s -p "Cloudflare Tunnel token (leave empty to configure HTTPS yourself): " \
    tunnel_token
  echo
  if [[ -n "${tunnel_token}" ]]; then
    tunnel_token_file="$(mktemp)"
    temporary_files+=("${tunnel_token_file}")
    chmod 0600 "${tunnel_token_file}"
    printf '%s' "${tunnel_token}" >"${tunnel_token_file}"
    unset tunnel_token
  fi
fi

validate_input_file() {
  local path="$1"
  local label="$2"
  if [[ ! -f "${path}" || -L "${path}" ]]; then
    echo "${label} must be a regular, non-symlink file: ${path}" >&2
    exit 2
  fi
}
[[ -z "${tunnel_token_file}" ]] || validate_input_file "${tunnel_token_file}" "tunnel token"
[[ -z "${backup_public_key}" ]] || validate_input_file "${backup_public_key}" "backup public key"

if [[ -z "${test_root}" ]]; then
  if [[ "${EUID}" -ne 0 ]]; then
    echo "run this installer with sudo" >&2
    exit 1
  fi
  if [[ ! -r /etc/os-release ]]; then
    echo "unable to identify the operating system" >&2
    exit 1
  fi
  # shellcheck disable=SC1091
  . /etc/os-release
  if [[ "${ID:-}" != ubuntu && "${ID:-}" != debian ]]; then
    echo "the Hub installer currently supports Ubuntu/Debian with systemd" >&2
    exit 1
  fi
  if [[ "$(ps -p 1 -o comm=)" != systemd ]]; then
    echo "systemd must be PID 1" >&2
    exit 1
  fi
else
  if [[ "${test_root}" != /* || "${test_root}" == / ]]; then
    echo "CAPTCHAMESH_TEST_ROOT must be a non-root absolute path" >&2
    exit 2
  fi
  mkdir -p "${test_root}"
fi

target_path() {
  printf '%s%s\n' "${test_root}" "$1"
}

install_owned() {
  local mode="$1"
  local owner="$2"
  local group="$3"
  local source="$4"
  local target="$5"
  if [[ -z "${test_root}" ]]; then
    install -o "${owner}" -g "${group}" -m "${mode}" "${source}" "${target}"
  else
    install -m "${mode}" "${source}" "${target}"
  fi
}

if [[ -z "${test_root}" ]]; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y ca-certificates curl openssl python3 python3-venv
  python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' || {
    echo "CaptchaMesh requires Python 3.11 or newer" >&2
    exit 1
  }
  if ! id -u captchamesh >/dev/null 2>&1; then
    useradd --system --home-dir /var/lib/captchamesh-hub --shell /usr/sbin/nologin \
      captchamesh
  fi
else
  python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'
fi

opt_root="$(target_path /opt/captchamesh-hub)"
state_root="$(target_path /var/lib/captchamesh-hub)"
config_root="$(target_path /etc/captchamesh-hub)"
unit_root="$(target_path /etc/systemd/system)"
mkdir -p "${opt_root}" "${state_root}" "${config_root}" "${unit_root}"
if [[ -z "${test_root}" ]]; then
  chown root:root "${opt_root}"
  chmod 0755 "${opt_root}"
  chown captchamesh:captchamesh "${state_root}"
  chmod 0700 "${state_root}"
  chown root:captchamesh "${config_root}"
  chmod 0750 "${config_root}"
else
  chmod 0755 "${opt_root}"
  chmod 0700 "${state_root}"
  chmod 0750 "${config_root}"
fi

for key_name in api node; do
  key_path="${config_root}/${key_name}.key"
  if [[ ! -s "${key_path}" ]]; then
    umask 0077
    openssl rand -hex 32 >"${key_path}"
  fi
  if [[ -z "${test_root}" ]]; then
    chown root:captchamesh "${key_path}"
  fi
  chmod 0640 "${key_path}"
done

cat >"${config_root}/hub.env" <<EOF
CAPTCHAMESH_ALLOWED_HOSTS=${domain},127.0.0.1,localhost
CAPTCHAMESH_ALLOW_PUBLIC_PAIRING=${public_pairing}
EOF
if [[ -z "${test_root}" ]]; then
  chown root:captchamesh "${config_root}/hub.env"
fi
chmod 0640 "${config_root}/hub.env"

for source_name in broker.py broker_asgi.py challenge_protocol.py relay_protocol.py \
  twocaptcha_compat.py requirements.txt; do
  install_owned 0644 root root "${project_root}/${source_name}" "${opt_root}/${source_name}"
done
if [[ ! -e "${opt_root}/registrations.json" ]]; then
  install_owned 0644 root root "${project_root}/tests/fixtures/empty_registrations.json" \
    "${opt_root}/registrations.json"
fi
install_owned 0644 root root "${script_dir}/captchamesh-hub.service" \
  "${unit_root}/captchamesh-hub.service"

if [[ ! -x "${opt_root}/.venv/bin/python" ]]; then
  python3 -m venv "${opt_root}/.venv"
fi
"${opt_root}/.venv/bin/pip" install --disable-pip-version-check -q \
  -r "${opt_root}/requirements.txt"

if [[ -n "${tunnel_token_file}" ]]; then
  if [[ -z "${test_root}" ]]; then
    apt-get install -y gnupg
    if ! command -v cloudflared >/dev/null 2>&1; then
      install -d -m 0755 /usr/share/keyrings
      curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg \
        -o /usr/share/keyrings/cloudflare-main.gpg
      chmod 0644 /usr/share/keyrings/cloudflare-main.gpg
      printf '%s\n' \
        'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' \
        >/etc/apt/sources.list.d/cloudflared.list
      apt-get update
      apt-get install -y cloudflared
    fi
    cloudflared_path="$(command -v cloudflared)"
    if ! id -u cloudflared >/dev/null 2>&1; then
      useradd --system --home-dir /nonexistent --shell /usr/sbin/nologin cloudflared
    fi
    tunnel_root=/etc/cloudflared
    install -d -o root -g cloudflared -m 0750 "${tunnel_root}"
    install -o root -g cloudflared -m 0640 "${tunnel_token_file}" \
      "${tunnel_root}/tunnel.token"
  else
    cloudflared_path=/usr/bin/cloudflared
    tunnel_root="$(target_path /etc/cloudflared)"
    mkdir -p "${tunnel_root}"
    chmod 0750 "${tunnel_root}"
    install -m 0640 "${tunnel_token_file}" "${tunnel_root}/tunnel.token"
  fi
  rendered_unit="$(mktemp)"
  temporary_files+=("${rendered_unit}")
  sed "s|@CLOUDFLARED@|${cloudflared_path}|g" "${script_dir}/cloudflared.service.in" \
    >"${rendered_unit}"
  install_owned 0644 root root "${rendered_unit}" "${unit_root}/cloudflared.service"
fi

if [[ -n "${backup_public_key}" ]]; then
  if [[ -z "${test_root}" ]]; then
    apt-get install -y gnupg sqlite3
  fi
  backup_root="$(target_path /var/backups/captchamesh-hub)"
  backup_state="$(target_path /var/lib/captchamesh-backup)"
  backup_config="$(target_path /etc/captchamesh-backup)"
  mkdir -p "${backup_root}" "${backup_state}/gnupg" "${backup_config}"
  chmod 0700 "${backup_root}" "${backup_state}" "${backup_state}/gnupg"
  chmod 0755 "${backup_config}"
  fingerprint="$(gpg --batch --show-keys --with-colons "${backup_public_key}" \
    | awk -F: '$1=="fpr"{print $10; exit}')"
  [[ "${fingerprint}" =~ ^[A-F0-9]{40}$ ]]
  GNUPGHOME="${backup_state}/gnupg" gpg --batch --import "${backup_public_key}" \
    >/dev/null 2>&1
  printf '%s\n' "${fingerprint}" >"${backup_config}/recipient"
  chmod 0640 "${backup_config}/recipient"
  if [[ -z "${test_root}" ]]; then
    chown -R captchamesh:captchamesh "${backup_root}" "${backup_state}"
    chown root:captchamesh "${backup_config}/recipient"
  fi
  mkdir -p "$(target_path /usr/local/sbin)"
  install_owned 0755 root root "${script_dir}/captchamesh-backup" \
    "$(target_path /usr/local/sbin/captchamesh-backup)"
  install_owned 0644 root root "${script_dir}/captchamesh-backup.service" \
    "${unit_root}/captchamesh-backup.service"
  install_owned 0644 root root "${script_dir}/captchamesh-backup.timer" \
    "${unit_root}/captchamesh-backup.timer"
fi

if [[ -z "${test_root}" ]]; then
  systemctl daemon-reload
  systemctl enable --now captchamesh-hub.service
  systemctl restart captchamesh-hub.service
  if [[ -n "${tunnel_token_file}" ]]; then
    systemctl enable --now cloudflared.service
    systemctl restart cloudflared.service
  fi
  if [[ -n "${backup_public_key}" ]]; then
    systemctl enable --now captchamesh-backup.timer
    systemctl start captchamesh-backup.service
  fi
  for attempt in $(seq 1 20); do
    curl -fsS http://127.0.0.1:8890/healthz >/dev/null && break
    [[ "${attempt}" == 20 ]] && {
      systemctl status captchamesh-hub.service --no-pager -l
      exit 1
    }
    sleep 0.5
  done
  if [[ "${skip_public_check}" == 0 && -n "${tunnel_token_file}" ]]; then
    for attempt in $(seq 1 30); do
      curl -fsS --max-time 5 "https://${domain}/healthz" >/dev/null && break
      if [[ "${attempt}" == 30 ]]; then
        echo "Hub is running locally, but https://${domain}/healthz is not reachable." >&2
        echo "Check that the Cloudflare Tunnel public hostname points to http://localhost:8890." >&2
        exit 4
      fi
      sleep 1
    done
  fi
else
  (
    cd "${opt_root}"
    env \
      PYTHONPATH="${opt_root}" \
      CAPTCHAMESH_DB_PATH="${state_root}/broker.db" \
      CAPTCHAMESH_REGISTRY_PATH="${opt_root}/registrations.json" \
      CAPTCHAMESH_API_KEY_FILE="${config_root}/api.key" \
      CAPTCHAMESH_NODE_KEY_FILE="${config_root}/node.key" \
      CAPTCHAMESH_ALLOWED_HOSTS="${domain},127.0.0.1,localhost" \
      "${opt_root}/.venv/bin/python" -c 'import broker_asgi; assert broker_asgi.app' \
      </dev/null
  )
fi

echo "CaptchaMesh Hub installed for https://${domain}"
echo "Upgrade: run this installer again; keys and database are preserved."
if [[ -z "${tunnel_token_file}" ]]; then
  echo "Next: expose http://127.0.0.1:8890 through your HTTPS reverse tunnel."
fi
