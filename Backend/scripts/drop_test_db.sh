#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# drop_test_db.sh — drop (idempotemment) la base de test.
#
# Meme garde-fou que create_test_db.sh : refuse tout nom de DB qui ne se
# termine pas par "_test" et bloque les noms reserves (gestionee, postgres,
# template0/1, vide).
#
# Variables d'environnement (memes defaults que create_test_db.sh) :
#   PGHOST=127.0.0.1
#   PGPORT=5433
#   PGUSER=gestionee
#   PGPASSWORD=gestionee_dev_2026
#   TEST_DB_NAME=gestionee_test
# ---------------------------------------------------------------------------
set -euo pipefail

PGHOST="${PGHOST:-127.0.0.1}"
PGPORT="${PGPORT:-5433}"
PGUSER="${PGUSER:-gestionee}"
export PGPASSWORD="${PGPASSWORD:-gestionee_dev_2026}"
TEST_DB_NAME="${TEST_DB_NAME:-gestionee_test}"

# Garde-fou : refuser tout nom de DB qui n'a pas le suffixe _test
if [[ ! "${TEST_DB_NAME}" =~ _test$ ]]; then
  echo "[drop_test_db] REFUS : TEST_DB_NAME='${TEST_DB_NAME}' ne se termine pas par '_test'." >&2
  echo "[drop_test_db] Refus de toucher a une base potentiellement de prod." >&2
  exit 2
fi
# Blocage explicite des noms reserves
case "${TEST_DB_NAME}" in
  gestionee|postgres|template0|template1|"")
    echo "[drop_test_db] REFUS : nom reserve ou vide : '${TEST_DB_NAME}'" >&2
    exit 2
    ;;
esac

echo "[drop_test_db] Cible : ${PGUSER}@${PGHOST}:${PGPORT}/${TEST_DB_NAME}"
psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d postgres -v ON_ERROR_STOP=1 \
  -c "DROP DATABASE IF EXISTS \"${TEST_DB_NAME}\";"
echo "[drop_test_db] OK."
