#!/usr/bin/env bash
# first_init hook: initializes the cluster once per data volume (superuser, password, optional extra database).
set -euo pipefail

prefix="${BH_PKG_PREFIX:?BH_PKG_PREFIX is not set (run through Shaker)}"
data="${prefix}/data/pgdata"
bin="${prefix}/bin"
conf="${prefix}/conf/postgresql.conf"

fail() { echo "postgres init: $*" >&2; exit 1; }

[[ -n "${BH_POSTGRES_PASSWORD:-}" ]] || fail "BH_POSTGRES_PASSWORD is not set"
if [[ -s "${data}/PG_VERSION" ]]; then
    echo "postgres init: ${data} already holds a cluster, nothing to do" >&2
    exit 0
fi

mkdir -p "${data}"
chmod 0700 "${data}"
pwfile="$(mktemp "${prefix}/run/pw.XXXXXX")"
trap 'rm -f "${pwfile}"' EXIT
printf '%s\n' "${BH_POSTGRES_PASSWORD}" > "${pwfile}"

# builtin C.UTF-8: no libc locale and no ICU needed (PostgreSQL 17+)
"${bin}/initdb" -D "${data}" -U "${BH_POSTGRES_USER}" --pwfile="${pwfile}" \
    --auth-local=scram-sha-256 --auth-host=scram-sha-256 \
    -E UTF8 --locale-provider=builtin --locale=C.UTF-8 >&2

if [[ -n "${BH_POSTGRES_DB:-}" && "${BH_POSTGRES_DB}" != "postgres" ]]; then
    # a private server on the socket only: nothing reaches it from the network
    "${bin}/pg_ctl" -D "${data}" -w -s -o "-c config_file=${conf} -c listen_addresses=''" start >&2
    trap '"${bin}/pg_ctl" -D "${data}" -m fast -w -s stop >&2 || true; rm -f "${pwfile}"' EXIT
    PGPASSWORD="${BH_POSTGRES_PASSWORD}" "${bin}/createdb" -h "${prefix}/run" -p "${BH_POSTGRES_PORT}" \
        -U "${BH_POSTGRES_USER}" "${BH_POSTGRES_DB}" >&2
    echo "postgres init: created database ${BH_POSTGRES_DB}" >&2
fi
