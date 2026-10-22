# Distillery builder image: Alpine/musl toolchain + the distillery CLI runtime.
# One image per variant (ALPINE_VERSION comes from config/config.yaml).
ARG ALPINE_VERSION=3.21
FROM alpine:${ALPINE_VERSION}

# Toolchain used by the package recipes. musl-dev (via build-base) ships libc.a,
# which is what makes fully static executables possible.
RUN apk add --no-cache \
      bash build-base linux-headers musl-dev binutils file patch \
      perl m4 autoconf automake libtool pkgconf cmake samurai bison flex re2c \
      coreutils findutils grep sed gawk tar xz bzip2 gzip zstd curl git ca-certificates \
      fakeroot libstdc++ \
      nodejs

# Python for the CLI: a uniform interpreter on every variant (Alpine 3.15 ships 3.9),
# installed with uv from its static musl binary.
ARG UV_VERSION=0.8.22
ARG PYTHON_VERSION=3.12
RUN curl -fsSL "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-$(uname -m)-unknown-linux-musl.tar.gz" \
      | tar -xz -C /usr/local/bin --strip-components=1 \
 && UV_PYTHON_INSTALL_DIR=/opt/python uv python install ${PYTHON_VERSION} \
 && uv venv --python ${PYTHON_VERSION} /opt/distillery-venv --relocatable
COPY requirements.txt /tmp/requirements.txt
RUN VIRTUAL_ENV=/opt/distillery-venv uv pip install -r /tmp/requirements.txt \
 && rm /tmp/requirements.txt

ENV PATH="/opt/distillery-venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LC_ALL=C

# Unprivileged user used by `executor: container` (matches option.build_user in config.yaml)
RUN addgroup -g 1024 bh && adduser -D -u 1024 -G bh -h /home/bh -s /bin/bash bh \
 && mkdir -p /opt/bytehawks && chown bh:bh /opt/bytehawks

WORKDIR /src
CMD ["python3", "-m", "scripts.cli", "--help"]
