FROM mcr.microsoft.com/devcontainers/base:ubuntu-24.04@sha256:d94c97dd9cacf183d0a6fd12a8e87b526e9e928307674ae9c94139139c0c6eae AS base
SHELL ["/bin/bash", "-lc"]

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y \
    git \
    curl \
    ca-certificates \
    build-essential \
    pkg-config \
    python3 \
    python3-venv \
    python3-pip \
    python3-dev \
    libgtk-3-dev \
    libgtk-3-bin \
    libwebkit2gtk-4.1-dev \
    libcairo2-dev \
    libpango1.0-dev \
    libgdk-pixbuf-2.0-dev \
    libglib2.0-dev \
    libglib2.0-bin \
    librsvg2-dev \
    cmake \
    ninja-build \
    tcl-dev \
    libgflags-dev \
    libgoogle-glog-dev \
    libboost-all-dev \
    libgtest-dev \
    flex \
    libeigen3-dev \
    libunwind-dev \
    libmetis-dev \
    libgmp-dev \
    bison \
    libhwloc-dev \
    libcurl4-openssl-dev \
    libtbb-dev \
    patchelf \
    jq \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js (LTS) and pnpm.
RUN curl -fsSL https://deb.nodesource.com/setup_lts.x | bash - \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs \
    && npm install -g pnpm \
    && rm -rf /var/lib/apt/lists/*

# Install Rust (for Tauri).
RUN curl https://sh.rustup.rs -sSf | sh -s -- -y

# Install Bazel
RUN wget https://github.com/bazelbuild/bazel/releases/download/8.5.0/bazel-8.5.0-linux-x86_64 -O /usr/local/bin/bazel \
    && chmod +x /usr/local/bin/bazel

# Install uv (Python package manager used by ecos/server).
RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && ln -s /root/.local/bin/uv /usr/local/bin/uv

ENV PATH="/root/.cargo/bin:${PATH}"
ENV PKG_CONFIG_PATH="/usr/lib/x86_64-linux-gnu/pkgconfig:/usr/lib/pkgconfig:/usr/share/pkgconfig"

# Configure git to use GitHub proxy for all operations
# RUN git config --global url."https://ghfast.top/https://github.com/".insteadOf "https://github.com/"

FROM base AS devcontainer

FROM mcr.microsoft.com/devcontainers/base:ubuntu-24.04@sha256:d94c97dd9cacf183d0a6fd12a8e87b526e9e928307674ae9c94139139c0c6eae AS runtime
SHELL ["/bin/bash", "-lc"]

ARG GITHUB_RELEASE_BASE="https://github.com"
ARG ECC_WHEEL_URL="${GITHUB_RELEASE_BASE}/openecos-projects/ecc/releases/download/v0.1.0-alpha.2/ecc-0.1.0a2-py3-none-any.whl"
ARG ECC_DREAMPLACE_WHEEL_URL="${GITHUB_RELEASE_BASE}/openecos-projects/ecc-dreamplace/releases/download/v0.1.0-alpha.2/ecc_dreamplace-0.1.0a2-py3-none-manylinux_2_34_x86_64.whl"
ARG ECC_TOOLS_WHEEL_URL="${GITHUB_RELEASE_BASE}/openecos-projects/ecc-tools/releases/download/v0.1.0-alpha.2/ecc_tools-0.1.0a2-py3-none-manylinux_2_34_x86_64.whl"
ARG OSS_CAD_SUITE_URL="${GITHUB_RELEASE_BASE}/YosysHQ/oss-cad-suite-build/releases/download/2026-01-22/oss-cad-suite-linux-x64-20260122.tgz"

RUN apt-get -o Acquire::http::Proxy=false -o Acquire::https::Proxy=false update \
    && DEBIAN_FRONTEND=noninteractive apt-get -o Acquire::http::Proxy=false -o Acquire::https::Proxy=false install -y \
    curl \
    ca-certificates \
    python3 \
    python3-venv \
    python3-pip \
    jq \
    libdbus-1-3 \
    libfontconfig1 \
    libgl1 \
    libglib2.0-0 \
    libice6 \
    libsm6 \
    libx11-6 \
    libx11-xcb1 \
    libxcb-cursor0 \
    libxcb-icccm4 \
    libxcb-image0 \
    libxcb-keysyms1 \
    libxcb-randr0 \
    libxcb-render-util0 \
    libxcb-shape0 \
    libxcb-shm0 \
    libxcb-sync1 \
    libxcb-xfixes0 \
    libxcb-xinerama0 \
    libxcb-xkb1 \
    libxcb1 \
    libxkbcommon-x11-0 \
    libxrender1 \
    && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && ln -s /root/.local/bin/uv /usr/local/bin/uv

WORKDIR /opt/ecc-flow

RUN apt-get -o Acquire::http::Proxy=false -o Acquire::https::Proxy=false update \
    && DEBIAN_FRONTEND=noninteractive apt-get -o Acquire::http::Proxy=false -o Acquire::https::Proxy=false install -y \
    libcairo2 \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /tmp/ecc-dist \
    && ECC_WHEEL_PATH="/tmp/ecc-dist/$(basename "${ECC_WHEEL_URL}")" \
    && ECC_DREAMPLACE_WHEEL_PATH="/tmp/ecc-dist/$(basename "${ECC_DREAMPLACE_WHEEL_URL}")" \
    && ECC_TOOLS_WHEEL_PATH="/tmp/ecc-dist/$(basename "${ECC_TOOLS_WHEEL_URL}")" \
    && curl -fL --retry 8 --retry-all-errors --retry-delay 5 --connect-timeout 20 --max-time 1800 \
        "${ECC_WHEEL_URL}" -o "${ECC_WHEEL_PATH}" \
    && curl -fL --retry 8 --retry-all-errors --retry-delay 5 --connect-timeout 20 --max-time 1800 \
        "${ECC_DREAMPLACE_WHEEL_URL}" -o "${ECC_DREAMPLACE_WHEEL_PATH}" \
    && curl -fL --retry 8 --retry-all-errors --retry-delay 5 --connect-timeout 20 --max-time 1800 \
        "${ECC_TOOLS_WHEEL_URL}" -o "${ECC_TOOLS_WHEEL_PATH}" \
    && printf 'ECC_WHEEL_PATH=%q\nECC_DREAMPLACE_WHEEL_PATH=%q\nECC_TOOLS_WHEEL_PATH=%q\n' \
        "${ECC_WHEEL_PATH}" "${ECC_DREAMPLACE_WHEEL_PATH}" "${ECC_TOOLS_WHEEL_PATH}" \
        > /tmp/ecc-wheel-paths.env

RUN source /tmp/ecc-wheel-paths.env \
    && cat > /tmp/ecc-overrides.txt <<EOF
ecc-dreamplace @ file://${ECC_DREAMPLACE_WHEEL_PATH}
ecc-tools @ file://${ECC_TOOLS_WHEEL_PATH}
EOF

RUN --mount=type=cache,target=/root/.cache/uv \
    source /tmp/ecc-wheel-paths.env \
    && uv venv --python 3.11 /opt/ecc-flow/.venv \
    && uv pip install \
        --python /opt/ecc-flow/.venv/bin/python \
        --torch-backend cpu \
        --overrides /tmp/ecc-overrides.txt \
        "${ECC_WHEEL_PATH}"

COPY ecc/chipcompiler/thirdparty/ecc-dreamplace/thirdparty/flute/lut.ICCAD2015 \
    /tmp/ecc-runtime-assets/flute/lut.ICCAD2015
COPY ecc/chipcompiler/thirdparty/ecc-dreamplace/thirdparty/NCTUgr.ICCAD2012 \
    /tmp/ecc-runtime-assets/NCTUgr.ICCAD2012

RUN /opt/ecc-flow/.venv/bin/python - <<'PY'
import json
import shutil
import site
from pathlib import Path

site_packages = Path(site.getsitepackages()[0])
thirdparty_root = site_packages / "thirdparty"
thirdparty_root.mkdir(parents=True, exist_ok=True)

shutil.copytree(
    Path("/tmp/ecc-runtime-assets/flute/lut.ICCAD2015"),
    thirdparty_root / "flute" / "lut.ICCAD2015",
    dirs_exist_ok=True,
)
shutil.copytree(
    Path("/tmp/ecc-runtime-assets/NCTUgr.ICCAD2012"),
    thirdparty_root / "NCTUgr.ICCAD2012",
    dirs_exist_ok=True,
)

config_path = Path(
    site_packages / "chipcompiler/tools/ecc_dreamplace/configs/dreamplace.json"
)
config = json.loads(config_path.read_text())
config["routability_opt_flag"] = 0
config_path.write_text(json.dumps(config, indent=4) + "\n")

assert (thirdparty_root / "flute/lut.ICCAD2015/POWV9.dat").is_file()
assert (thirdparty_root / "NCTUgr.ICCAD2012/NCTUgr").is_file()
assert json.loads(config_path.read_text())["routability_opt_flag"] == 0
PY

RUN mkdir -p /opt/oss-cad-suite \
    && curl -fL --retry 8 --retry-all-errors --retry-delay 5 --connect-timeout 20 --max-time 1800 \
        "${OSS_CAD_SUITE_URL}" | tar -xz -C /opt/oss-cad-suite --strip-components=1 \
    && test -x /opt/oss-cad-suite/bin/yosys

RUN cat > /etc/profile.d/oss-cad-suite.sh <<'EOF'
if [ -f /opt/oss-cad-suite/environment ]; then
  . /opt/oss-cad-suite/environment >/dev/null 2>&1 || true
fi
EOF

ENV VIRTUAL_ENV="/opt/ecc-flow/.venv"
ENV PATH="/opt/ecc-flow/.venv/bin:/opt/oss-cad-suite/bin:${PATH}"
ENV CHIPCOMPILER_OSS_CAD_DIR="/opt/oss-cad-suite"
ENV CHIPCOMPILER_ICS55_PDK_ROOT="/pdk"
ENV UV_LINK_MODE="copy"

WORKDIR /work

RUN python3 --version \
    && uv --version \
    && cli --help >/dev/null \
    && bash -lc 'source /opt/oss-cad-suite/environment && yosys -p "plugin -i slang" >/tmp/yosys-slang.log' \
    && rm -f /tmp/yosys-slang.log


# Build commands:
# docker build --target runtime -f .devcontainer/Dockerfile -t ecos-studio:latest .
# docker build --target devcontainer -f .devcontainer/Dockerfile -t ecos-studio-dev .
CMD ["bash"]
