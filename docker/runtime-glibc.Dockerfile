# syntax=docker/dockerfile:1
#
# Runtime base, glibc: what `autobuilder.py image --entrypoint shaker --base` builds on. Shaker needs bash and the usual coreutils-class tools;
# the slim Debian image has them, this adds CA certificates and time zones. No compiler, no Python: the services are already built.
#
#   docker build -f docker/runtime-glibc.Dockerfile -t bytehawks-runtime:glibc .
#   docker save bytehawks-runtime:glibc | tar -x -C /tmp/base          # an OCI layout for --base /tmp/base
ARG BASE=debian:12-slim
FROM ${BASE}
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates tzdata \
 && rm -rf /var/lib/apt/lists/*
LABEL org.opencontainers.image.title="bytehawks-runtime" \
      org.opencontainers.image.description="Base for Distillery service images in Shaker mode (glibc)" \
      org.opencontainers.image.source="https://github.com/bytehawks-org/distillery"
