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

ARG OSS_CAD_SUITE_URL="https://github.com/YosysHQ/oss-cad-suite-build/releases/download/2026-01-22/oss-cad-suite-linux-x64-20260122.tgz"

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y \
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

RUN cat > /tmp/ecc-overrides.txt <<'EOF'
ecc-dreamplace @ https://github.com/openecos-projects/ecc-dreamplace/releases/download/v0.1.0-alpha.2/ecc_dreamplace-0.1.0a2-py3-none-manylinux_2_34_x86_64.whl
ecc-tools @ https://github.com/openecos-projects/ecc-tools/releases/download/v0.1.0-alpha.2/ecc_tools-0.1.0a2-py3-none-manylinux_2_34_x86_64.whl
EOF

RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv --python 3.11 /opt/ecc-flow/.venv \
    && uv pip install \
        --python /opt/ecc-flow/.venv/bin/python \
        --torch-backend cpu \
        --overrides /tmp/ecc-overrides.txt \
        "ecc @ https://github.com/openecos-projects/ecc/releases/download/v0.1.0-alpha.2/ecc-0.1.0a2-py3-none-any.whl"

RUN mkdir -p /opt/oss-cad-suite \
    && curl -L "${OSS_CAD_SUITE_URL}" | tar -xz -C /opt/oss-cad-suite --strip-components=1 \
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
