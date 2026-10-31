# syntax=docker/dockerfile:1
#
# Runtime base, musl (Alpine): the same for musl artifacts. bash, coreutils, util-linux (setpriv), tar and gnupg for signature checks.
#
#   docker build -f docker/runtime-musl.Dockerfile -t bytehawks-runtime:musl .
ARG BASE=alpine:3.20
FROM ${BASE}
RUN apk add --no-cache bash coreutils grep sed findutils util-linux tar ca-certificates tzdata
LABEL org.opencontainers.image.title="bytehawks-runtime" \
      org.opencontainers.image.description="Base for Distillery service images in Shaker mode (musl)" \
      org.opencontainers.image.source="https://github.com/bytehawks-org/distillery"
