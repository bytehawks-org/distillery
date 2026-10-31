# syntax=docker/dockerfile:1
#
# Builder image, musl (Alpine): the same as builder-glibc.Dockerfile for the musl artifacts (amd64-musl, amd64-musl-static) and the apk packages.
#
#   docker build -f docker/builder-musl.Dockerfile -t bytehawks-builder:musl .
#   docker run --rm -v "$PWD":/work -w /work bytehawks-builder:musl python scripts/autobuilder.py build --package nginx --with-deps --libc musl
ARG BASE=alpine:3.20
FROM ${BASE}

ARG TARGETARCH
# keep these in step with the nfpm entry of packages.yaml (tests/test_docker.py checks it)
ARG NFPM_VERSION=2.48.0
ARG NFPM_SHA256_AMD64=6d7f9a28b2f397934403950fcbba83f598b08d02edbe79a0815b3b4897341750
ARG NFPM_SHA256_ARM64=a9a75213763783b7669719e505474c748c5ad7dc66591a195619deb50f85c2f4

RUN apk add --no-cache \
      bash build-base linux-headers perl python3 py3-virtualenv git curl ca-certificates jq gnupg tar bubblewrap \
      flex bison m4 pkgconf patch xz unzip zip file coreutils

COPY scripts/requirements.txt /tmp/requirements.txt
RUN python3 -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt \
 && rm /tmp/requirements.txt

RUN set -eux; \
    case "${TARGETARCH:-$(apk --print-arch | sed 's/x86_64/amd64/; s/aarch64/arm64/')}" in \
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
      org.opencontainers.image.description="Distillery builder image (musl): toolchain, python, gpg, bubblewrap, nfpm" \
      org.opencontainers.image.source="https://github.com/bytehawks-org/distillery"
WORKDIR /work
