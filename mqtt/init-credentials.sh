#!/usr/bin/env bash
#
# Creates mqtt/pwfile (hashed credentials, gitignored) and prints the block to
# paste into configuration.env.
#
# The broker refuses anonymous clients once mosquitto.conf sets
# `allow_anonymous false`, so this must run BEFORE the next `docker compose up`.
# Skip it and every service is rejected with rc=5: the containers stay up, the
# dashboard renders, and no data moves — a failure that looks like nothing.
#
# Requires: docker, openssl. mosquitto_passwd is taken from the broker image, so
# nothing has to be installed on the host.

set -euo pipefail

cd "$(dirname "$0")/.."
PWFILE="mqtt/pwfile"
IMAGE="eclipse-mosquitto:2.0"

# Keep in sync with mqtt/aclfile — an account here without a rule there can
# connect but cannot read or write anything.
ACCOUNTS=(gateway telegraf monitor p1 p2)

if [[ -e "$PWFILE" ]]; then
    echo "error: $PWFILE already exists." >&2
    echo "Rotating every credential at once means updating configuration.env" >&2
    echo "and all three Pis in the same window. Delete it deliberately first." >&2
    exit 1
fi

declare -A PASS
for account in "${ACCOUNTS[@]}"; do
    # Strip characters that would need quoting inside an env file.
    PASS[$account]=$(openssl rand -base64 32 | tr -dc 'A-Za-z0-9' | head -c 24)
done

# -c creates the file and must be used exactly once, for the first account.
#
# Two things this invocation gets right the hard way:
#   * mosquitto_passwd does NOT accept combined short options. "-cb" is rejected
#     with a usage dump, so -c and -b must be separate arguments.
#   * --entrypoint bypasses the image's docker-entrypoint.sh, which runs
#     `chown -R mosquitto:mosquitto /mosquitto` and would hand the host's mqtt/
#     directory to uid 1883 — after which nobody can edit mosquitto.conf without
#     sudo. Running the binary directly leaves ownership alone.
first=1
for account in "${ACCOUNTS[@]}"; do
    if [[ $first == 1 ]]; then args=(-c -b); first=0; else args=(-b); fi
    docker run --rm \
        --entrypoint mosquitto_passwd \
        -v "$PWD/mqtt:/mosquitto/config" "$IMAGE" \
        "${args[@]}" /mosquitto/config/pwfile \
        "$account" "${PASS[$account]}"
done

# Mosquitto 2.x warns on world-readable credential files and future versions will
# refuse to load them. Both must be readable by the broker's uid (1883), which is
# not the host user — hence the root container rather than a plain chmod.
docker run --rm -v "$PWD/mqtt:/x" alpine:3 sh -c \
    'chown 1883:1883 /x/pwfile /x/aclfile && chmod 0600 /x/pwfile /x/aclfile'

cat <<EOF

Created $PWFILE with ${#ACCOUNTS[@]} accounts.

Paste into configuration.env (replacing the change_me placeholders):

MQTT_USERNAME_GATEWAY=gateway
MQTT_PASSWORD_GATEWAY=${PASS[gateway]}

MQTT_USERNAME_TELEGRAF=telegraf
MQTT_PASSWORD_TELEGRAF=${PASS[telegraf]}

MQTT_USERNAME_MONITOR=monitor
MQTT_PASSWORD_MONITOR=${PASS[monitor]}

MQTT_PASSWORD_P1=${PASS[p1]}
MQTT_PASSWORD_P2=${PASS[p2]}

The p1/p2 passwords belong on the proxy-site Pis, not in this file — they are
printed here only because this is the one moment they exist in plaintext.

Then: docker compose up -d --force-recreate
Verify:  docker logs telegraf | grep -i "connect"
         docker logs meshtastic-testbed-gateway | grep MQTT
A rc=5 in either means the credential did not match.
EOF
