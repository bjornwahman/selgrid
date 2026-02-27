#!/usr/bin/env bash
set -euo pipefail

SELENIUM_DIR="${SELENIUM_DIR:-$(pwd)/.selenium}"
SELENIUM_VERSION="${SELENIUM_VERSION:-4.27.0}"
GRID_PORT="${GRID_PORT:-4444}"
SELENIUM_JAR="$SELENIUM_DIR/selenium-server-${SELENIUM_VERSION}.jar"

if [ ! -f "$SELENIUM_JAR" ]; then
  echo "Selenium Server hittades inte: $SELENIUM_JAR"
  echo "Kör först: scripts/install_local_grid.sh"
  exit 1
fi

exec java -jar "$SELENIUM_JAR" standalone --port "$GRID_PORT"
