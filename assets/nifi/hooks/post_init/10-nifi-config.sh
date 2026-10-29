#!/usr/bin/env bash
# post_init hook: renders the resolved BH_NIFI_* parameters into the NiFi configuration.
# Idempotent and run at every start: conf/ is derived state, durable data lives in BH_NIFI_DATA_DIR.
set -euo pipefail

prefix="${BH_PKG_PREFIX:?BH_PKG_PREFIX is not set (run through Shaker)}"
conf="${prefix}/conf"
data="${BH_NIFI_DATA_DIR:?}"
export JAVA_HOME="${JAVA_HOME:-${BH_NIFI_JAVA_HOME:-}}"

fail() { echo "nifi config: $*" >&2; exit 1; }

# set_prop FILE KEY VALUE - replace "KEY=..." in a .properties/.conf file, append when missing
set_prop() {
    local file="$1" key="$2" value="$3" pattern escaped
    pattern="^${key//./\\.}="
    escaped="$(printf '%s' "${value}" | sed -e 's/[\\|&]/\\&/g')"
    if grep -q "${pattern}" "${file}"; then
        sed -i "s|${pattern}.*|${key}=${escaped}|" "${file}"
    else
        printf '%s=%s\n' "${key}" "${value}" >> "${file}"
    fi
}

[[ ${#BH_NIFI_PASSWORD} -ge 12 ]] || fail "BH_NIFI_PASSWORD must have at least 12 characters"
[[ ${#BH_NIFI_SENSITIVE_PROPS_KEY} -ge 12 ]] || fail "BH_NIFI_SENSITIVE_PROPS_KEY must have at least 12 characters"
[[ -x "${JAVA_HOME}/bin/java" ]] || fail "no java in JAVA_HOME='${JAVA_HOME}' (BH_NIFI_JAVA_HOME)"

props="${conf}/nifi.properties"
mkdir -p "${data}/conf" "${data}/state" "${data}/database_repository" "${data}/flowfile_repository" \
         "${data}/content_repository" "${data}/provenance_repository" "${data}/status_repository" \
         "${data}/nar_repository" "${data}/assets" "${data}/connector_assets"

# web endpoint
set_prop "${props}" nifi.web.https.host "${BH_NIFI_HOST}"
set_prop "${props}" nifi.web.https.port "${BH_NIFI_PORT}"
set_prop "${props}" nifi.web.proxy.host "${BH_NIFI_PROXY_HOSTS:-}"
set_prop "${props}" nifi.sensitive.props.key "${BH_NIFI_SENSITIVE_PROPS_KEY}"

# durable state (flow, repositories, local state) outside conf/
set_prop "${props}" nifi.flow.configuration.file "${data}/conf/flow.json.gz"
set_prop "${props}" nifi.flow.configuration.archive.dir "${data}/conf/archive"
set_prop "${props}" nifi.database.directory "${data}/database_repository"
set_prop "${props}" nifi.flowfile.repository.directory "${data}/flowfile_repository"
set_prop "${props}" nifi.content.repository.directory.default "${data}/content_repository"
set_prop "${props}" nifi.provenance.repository.directory.default "${data}/provenance_repository"
set_prop "${props}" nifi.status.repository.questdb.persist.location "${data}/status_repository"
set_prop "${props}" nifi.nar.persistence.provider.properties.directory "${data}/nar_repository"
set_prop "${props}" nifi.asset.manager.properties.directory "${data}/assets"
set_prop "${props}" nifi.connector.asset.manager.properties.directory "${data}/connector_assets"
sed -i "s|<property name=\"Directory\">[^<]*</property>|<property name=\"Directory\">${data}/state/local</property>|" \
    "${conf}/state-management.xml"

# JVM heap
set_prop "${conf}/bootstrap.conf" java.arg.2 "-Xms${BH_NIFI_JVM_HEAP_INIT}"
set_prop "${conf}/bootstrap.conf" java.arg.3 "-Xmx${BH_NIFI_JVM_HEAP_MAX}"

# single user credentials (stored hashed in login-identity-providers.xml)
"${prefix}/bin/nifi.sh" set-single-user-credentials "${BH_NIFI_USERNAME}" "${BH_NIFI_PASSWORD}" >/dev/null

# the hook may run as root while NiFi runs as uid 1001, gid 0 (OpenShift convention): group access is what counts
chmod -R g+rwX "${conf}" "${data}"
