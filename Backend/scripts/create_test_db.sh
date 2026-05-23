#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# create_test_db.sh — crée (idempotemment) la base `gestionee_test` utilisee
# par la suite d'integration tests (Module 0).
#
# Pre-requis :
#   * Postgres deja en route sur ${PGHOST}:${PGPORT}
#   * Role ${PGUSER} avec droit CREATEDB
#   * Optionnel : extension PostGIS disponible cote serveur (si presente, on
#     l'active automatiquement sur gestionee_test ; sinon on emet juste un
#     warning, le harness de tests sait fonctionner sans).
#
# Variables d'environnement supportees (avec defaults pour l'env local) :
#   PGHOST=127.0.0.1
#   PGPORT=5433
#   PGUSER=gestionee
#   PGPASSWORD=gestionee_dev_2026
#   TEST_DB_NAME=gestionee_test
#
# Usage :
#   bash scripts/create_test_db.sh             # cree la DB si absente
#   DROP=1 bash scripts/create_test_db.sh      # drop + recree
# ---------------------------------------------------------------------------
set -euo pipefail

PGHOST="${PGHOST:-127.0.0.1}"
PGPORT="${PGPORT:-5433}"
PGUSER="${PGUSER:-gestionee}"
export PGPASSWORD="${PGPASSWORD:-gestionee_dev_2026}"
TEST_DB_NAME="${TEST_DB_NAME:-gestionee_test}"

# Garde-fou : refuser tout nom de DB qui n'a pas le suffixe _test
if [[ ! "${TEST_DB_NAME}" =~ _test$ ]]; then
  echo "[create_test_db] REFUS : TEST_DB_NAME='${TEST_DB_NAME}' ne se termine pas par '_test'." >&2
  echo "[create_test_db] Refus de toucher a une base potentiellement de prod." >&2
  exit 2
fi
# Blocage explicite des noms reserves
case "${TEST_DB_NAME}" in
  gestionee|postgres|template0|template1|"")
    echo "[create_test_db] REFUS : nom reserve ou vide : '${TEST_DB_NAME}'" >&2
    exit 2
    ;;
esac

psql_admin() {
  # On se connecte a la base "postgres" pour pouvoir DROP/CREATE la DB cible.
  psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d postgres -v ON_ERROR_STOP=1 "$@"
}

psql_test() {
  psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$TEST_DB_NAME" -v ON_ERROR_STOP=1 "$@"
}

echo "[create_test_db] Cible : ${PGUSER}@${PGHOST}:${PGPORT}/${TEST_DB_NAME}"

# Drop optionnel (si on veut repartir d'une DB vierge).
if [[ "${DROP:-0}" == "1" ]]; then
  echo "[create_test_db] DROP=1 -> suppression de ${TEST_DB_NAME} si existe."
  psql_admin -c "DROP DATABASE IF EXISTS \"${TEST_DB_NAME}\";"
fi

# Cree la DB si elle n'existe pas. La requete renvoie 1 ligne si elle existe.
DB_EXISTS=$(psql_admin -tAc \
  "SELECT 1 FROM pg_database WHERE datname='${TEST_DB_NAME}'" || true)

if [[ "${DB_EXISTS}" == "1" ]]; then
  echo "[create_test_db] La DB ${TEST_DB_NAME} existe deja."
else
  echo "[create_test_db] Creation de la DB ${TEST_DB_NAME}."
  psql_admin -c "CREATE DATABASE \"${TEST_DB_NAME}\" OWNER \"${PGUSER}\";"
fi

# Active PostGIS si disponible cote serveur (ne plante pas si absent — la
# conftest est concue pour fonctionner sans, en repliant la colonne geom).
PG_HAS_POSTGIS=$(psql_admin -tAc \
  "SELECT 1 FROM pg_available_extensions WHERE name='postgis'" || true)

if [[ "${PG_HAS_POSTGIS}" == "1" ]]; then
  echo "[create_test_db] PostGIS disponible -> CREATE EXTENSION IF NOT EXISTS postgis."
  psql_test -c "CREATE EXTENSION IF NOT EXISTS postgis;" || \
    echo "[create_test_db] WARN : CREATE EXTENSION a echoue (droits ?). On continue."
else
  echo "[create_test_db] WARN : PostGIS non installe sur ce serveur Postgres."
  echo "[create_test_db]        Les tests qui dependent du type Geography seront skip."
  echo "[create_test_db]        Pour l'activer : brew install postgis (ou paquet OS)."
fi

echo "[create_test_db] OK."
