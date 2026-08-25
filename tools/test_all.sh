#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"

security=0
if [ "${1:-}" = "--security" ]; then
  security=1
elif [ "$#" -ne 0 ]; then
  echo "usage: $0 [--security]" >&2
  exit 2
fi

python_bin=.venv/bin/python
if [ ! -x "$python_bin" ]; then
  echo "缺少 .venv；先运行 README 中的环境准备命令。" >&2
  exit 1
fi
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "完整测试需要在 git clone 得到的 CaptchaMesh 仓库中运行。" >&2
  exit 1
fi

echo "[1/8] Python tests"
"$python_bin" -m unittest discover -s tests -v

echo "[2/8] Public-source static analysis"
git ls-files -co --exclude-standard -z -- '*.py' \
  | grep -zv '^tests/' \
  | xargs -0 .venv/bin/bandit -q
.venv/bin/pip-audit --vulnerability-service osv -r requirements.txt

echo "[3/8] Python distributions"
test_root=$(mktemp -d)
trap 'rm -rf -- "$test_root"' EXIT HUP INT TERM
build_dir=$test_root/dist
"$python_bin" -m build --outdir "$build_dir"
"$python_bin" tools/verify_public_release.py "$build_dir"

echo "[4/8] Installed CLI smoke test"
smoke_dir=$test_root/install
venv_site=$("$python_bin" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')
"$python_bin" -m pip install --disable-pip-version-check --no-deps \
  --target "$smoke_dir" "$build_dir"/*.whl >/dev/null
(
  cd "$smoke_dir"
  PYTHONPATH="$smoke_dir:$venv_site" \
    "$project_dir/$python_bin" -m captchamesh_cli --help >/dev/null
)

echo "[5/8] Android tests, lint and APK"
(
  cd app-src
  ./gradlew --no-daemon :app:testDebugUnitTest :app:lintDebug :app:assembleDebug
)

echo "[6/8] Release boundary recheck"
"$python_bin" tools/verify_public_release.py "$build_dir"

echo "[7/8] Hub release bundle"
hub_dir=$test_root/hub
mkdir -p "$hub_dir"
"$python_bin" tools/build_hub_bundle.py --output "$hub_dir"
bash -n deploy/hub/install.sh deploy/hub/captchamesh-backup

echo "[8/8] Git history secret scan"
if [ "$security" -eq 1 ]; then
  if ! command -v gitleaks >/dev/null 2>&1; then
    echo "--security 需要先安装 gitleaks。" >&2
    exit 1
  fi
  gitleaks git . --no-banner --redact --exit-code 1
else
  echo "跳过；安装 gitleaks 后运行 $0 --security。"
fi

echo "CaptchaMesh full test suite passed"
