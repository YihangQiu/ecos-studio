#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT_DIR"

IMAGE_TAG=${IMAGE_TAG:-ecos-studio-verify:latest}
RUN_GCD=${RUN_GCD:-1}
RUN_SOC=${RUN_SOC:-0}
RUN_RETROSOC=${RUN_RETROSOC:-0}
UPDATE_SUBMODULES=${UPDATE_SUBMODULES:-0}
USE_SSH_KEY=${USE_SSH_KEY:-0}
SSH_DIR=${SSH_DIR:-$HOME/.ssh}
USE_PROXY=${USE_PROXY:-false}
GH_PROXY=${GH_PROXY:-https://gh-proxy.org/}
TOOL=${TOOL:-}

if [ "${RUN_GCD}" != "1" ] && [ "${RUN_SOC}" != "1" ] && [ "${RUN_RETROSOC}" != "1" ]; then
  echo "[error] At least one demo must be enabled: RUN_GCD=1 and/or RUN_SOC=1 and/or RUN_RETROSOC=1" >&2
  exit 1
fi

echo "[docker] build clean verification image: ${IMAGE_TAG}"
docker build --no-cache -f Dockerfile.verify -t "${IMAGE_TAG}" .

echo "[docker] run clean demo verification in container"
docker_args=(
  --rm
  -v "$ROOT_DIR":/workspace
  -w /workspace
  -e "RUN_GCD=${RUN_GCD}"
  -e "RUN_SOC=${RUN_SOC}"
  -e "RUN_RETROSOC=${RUN_RETROSOC}"
  -e "UPDATE_SUBMODULES=${UPDATE_SUBMODULES}"
  -e "USE_SSH_KEY=${USE_SSH_KEY}"
  -e "USE_PROXY=${USE_PROXY}"
  -e "GH_PROXY=${GH_PROXY}"
  -e "TOOL=${TOOL}"
)

if [ "${USE_SSH_KEY}" = "1" ]; then
  if [ ! -d "${SSH_DIR}" ]; then
    echo "[error] SSH_DIR not found: ${SSH_DIR}" >&2
    exit 1
  fi
  echo "[docker] mounting SSH directory (read-only): ${SSH_DIR}"
  docker_args+=(
    -v "${SSH_DIR}:/root/.ssh:ro"
    -e GIT_SSH_COMMAND="ssh -o StrictHostKeyChecking=accept-new"
  )
fi

docker run "${docker_args[@]}" \
  "${IMAGE_TAG}" \
  bash -lc '
    set -euo pipefail

    git config --global --add safe.directory "*"
    git config --global --add safe.directory /workspace

    if [ "${USE_SSH_KEY}" != "1" ]; then
      git config --global url."https://github.com/".insteadOf git@github.com:
      git config --global url."https://github.com/".insteadOf ssh://git@github.com/
    fi

    rm -rf ws/gcd ws/soc ws/retrosoc

    if [ "${UPDATE_SUBMODULES}" = "1" ]; then
      while read -r key url; do
        https_url="${url/git@github.com:/https://github.com/}"
        https_url="${https_url/ssh:\/\/git@github.com\//https://github.com/}"
        git config "$key" "$https_url"
      done < <(git config -f .gitmodules --get-regexp "^submodule\..*\.url$")
      git submodule sync --recursive
      make setup ECC_CLI=path:./eda/ecc\#cli USE_PROXY="${USE_PROXY}" GH_PROXY="${GH_PROXY}" TOOL="${TOOL}"
    else
      make setup-pdk install-ecc ECC_CLI=path:./eda/ecc\#cli USE_PROXY="${USE_PROXY}" GH_PROXY="${GH_PROXY}" TOOL="${TOOL}"
    fi

    if [ "${RUN_GCD}" = "1" ]; then
      make demo-gcd ECC_CLI=path:./eda/ecc\#cli

      test -d ws/gcd/Synthesis_yosys
      test -d ws/gcd/Floorplan_ecc
      test -d ws/gcd/route_ecc
    fi

    if [ "${RUN_SOC}" = "1" ]; then
      make demo-soc ECC_CLI=path:./eda/ecc\#cli

      test -d ws/soc/Synthesis_yosys
      test -d ws/soc/Floorplan_ecc
      test -d ws/soc/route_ecc
    fi

    if [ "${RUN_RETROSOC}" = "1" ]; then
      make demo-retrosoc ECC_CLI=path:./eda/ecc\#cli

      test -d ws/retrosoc/Synthesis_yosys
      test -d ws/retrosoc/Floorplan_ecc
      test -d ws/retrosoc/route_ecc
    fi

    verified=()
    if [ "${RUN_GCD}" = "1" ]; then
      verified+=(gcd)
    fi
    if [ "${RUN_SOC}" = "1" ]; then
      verified+=(soc)
    fi
    if [ "${RUN_RETROSOC}" = "1" ]; then
      verified+=(retrosoc)
    fi
    echo "[verify] ${verified[*]} demo workspace(s) contain stage directories."
  '

echo "[done] docker demo verification passed"
