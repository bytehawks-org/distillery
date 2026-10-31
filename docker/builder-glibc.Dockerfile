# syntax=docker/dockerfile:1
#
# Builder image, glibc: everything a Distillery build or a CI job needs, so a job does not install a toolchain every time.
#   toolchain (gcc, make, perl, flex/bison...), python + PyYAML/ruamel in /opt/venv, git, gpg, jq, bubblewrap (the --sandbox backend),
#   nfpm (deb/rpm/apk packages, pinned and checksum-verified).
# Build on the OLDEST glibc you intend to support: binaries run on newer ones, not on older ones.
#
#   docker build -f docker/builder-glibc.Dockerfile -t bytehawks-builder:glibc .
#   docker run --rm -v "$PWD":/work -w /work bytehawks-builder:glibc python scripts/autobuilder.py chain --store dir:store
#
# Set BH_BUILDER_IMAGE to the image reference when you run it: Distillery records it in every manifest.
ARG BASE=debian:12
FROM ${BASE}

ARG TARGETARCH
# keep these in step with the nfpm entry of packages.yaml (tests/test_docker.py checks it)
ARG NFPM_VERSION=2.48.0
ARG NFPM_SHA256_AMD64=6d7f9a28b2f397934403950fcbba83f598b08d02edbe79a0815b3b4897341750
ARG NFPM_SHA256_ARM64=a9a75213763783b7669719e505474c748c5ad7dc66591a195619deb50f85c2f4

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential perl python3 python3-venv git curl ca-certificates jq gnupg bubblewrap \
      flex bison m4 pkg-config patch xz-utils unzip zip file \
 && rm -rf /var/lib/apt/lists/*

COPY scripts/requirements.txt /tmp/requirements.txt
RUN python3 -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt \
 && rm /tmp/requirements.txt

RUN set -eux; \
    case "${TARGETARCH:-$(dpkg --print-architecture)}" in \
      amd64) arch=x86_64; sha="${NFPM_SHA256_AMD64}" ;; \
      arm64) arch=arm64;  sha="${NFPM_SHA256_ARM64}" ;; \
      *) echo "unsupported architecture ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/nfpm.tar.gz "https://github.com/goreleaser/nfpm/releases/download/v${NFPM_VERSION}/nfpm_${NFPM_VERSION}_Linux_${arch}.tar.gz"; \
    echo "${sha}  /tmp/nfpm.tar.gz" | sha256sum -c -; \
    tar -C /usr/local/bin -xzf /tmp/nfpm.tar.gz nfpm; \
    rm /tmp/nfpm.tar.gz; \
    nfpm --version

ENV PATH=/opt/venv/bin:${PATH}
LABEL org.opencontainers.image.title="bytehawks-builder" \
      org.opencontainers.image.description="Distillery builder image (glibc): toolchain, python, gpg, bubblewrap, nfpm" \
      org.opencontainers.image.source="https://github.com/bytehawks-org/distillery"
WORKDIR /work
