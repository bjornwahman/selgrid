#!/usr/bin/env bash
set -euo pipefail

SELENIUM_DIR="${SELENIUM_DIR:-$(pwd)/.selenium}"
SELENIUM_VERSION="${SELENIUM_VERSION:-4.27.0}"
SELENIUM_JAR="$SELENIUM_DIR/selenium-server-${SELENIUM_VERSION}.jar"
SELENIUM_URL="https://github.com/SeleniumHQ/selenium/releases/download/selenium-${SELENIUM_VERSION}/selenium-server-${SELENIUM_VERSION}.jar"

mkdir -p "$SELENIUM_DIR"

if ! command -v java >/dev/null 2>&1; then
  echo "Java saknas. Installera Java 17+ och kör scriptet igen."
  echo "Debian/Ubuntu exempel: sudo apt-get update && sudo apt-get install -y openjdk-17-jre"
  exit 1
fi

if [ ! -f "$SELENIUM_JAR" ]; then
  echo "Laddar ner Selenium Server ${SELENIUM_VERSION}..."
  curl -fL "$SELENIUM_URL" -o "$SELENIUM_JAR"
else
  echo "Selenium Server finns redan: $SELENIUM_JAR"
fi

if ! command -v google-chrome >/dev/null 2>&1 && ! command -v chromium >/dev/null 2>&1 && ! command -v chromium-browser >/dev/null 2>&1; then
  echo "Ingen Chrome/Chromium hittades på servern."
  echo "Installera en browser innan tester körs mot Grid."
  echo "Debian/Ubuntu exempel: sudo apt-get install -y chromium"
fi

echo "Klar. Starta Grid med: scripts/start_local_grid.sh"
