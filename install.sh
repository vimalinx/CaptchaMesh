#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
install_root=${XDG_DATA_HOME:-"$HOME/.local/share"}/captchamesh
venv_dir=$install_root/venv
bin_dir=${XDG_BIN_HOME:-"$HOME/.local/bin"}
config_dir=${XDG_CONFIG_HOME:-"$HOME/.config"}/captchamesh
launcher=$bin_dir/captchamesh

python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' || {
  echo "CaptchaMesh 需要 Python 3.11 或更高版本。" >&2
  exit 1
}

mkdir -p "$install_root" "$bin_dir" "$config_dir"
chmod 700 "$config_dir"
legacy_state=$project_dir/.secrets/relay-pairing.json
installed_state=$config_dir/relay-pairing.json
if [ -f "$legacy_state" ] && [ ! -e "$installed_state" ]; then
  install -m 600 "$legacy_state" "$installed_state"
  echo "已把现有手机配对迁移到 $installed_state"
fi
if [ ! -x "$venv_dir/bin/python" ]; then
  python3 -m venv "$venv_dir"
fi
"$venv_dir/bin/python" -m pip install --upgrade "$project_dir"
"$venv_dir/bin/captchamesh" skill install

if [ -e "$launcher" ] && [ ! -L "$launcher" ]; then
  echo "$launcher 已存在且不是符号链接，未覆盖。" >&2
  exit 1
fi
ln -sfn "$venv_dir/bin/captchamesh" "$launcher"

echo "CaptchaMesh 已安装：$launcher"
echo "CaptchaMesh Agent Skill 已安装；可运行 captchamesh skill status 检查。"
echo "运行 captchamesh start，然后打开终端显示的本机配对链接。"
