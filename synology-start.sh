#!/usr/bin/env bash
set -euo pipefail

: "${GITHUB_REPOSITORY:?Missing GITHUB_REPOSITORY (owner/repo)}"
: "${GITHUB_REF:=main}"
: "${APP_SECRET:?Missing APP_SECRET}"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends git python3 python3-pip supervisor ca-certificates tzdata
rm -rf /var/lib/apt/lists/*

mkdir -p /opt/selgrid /opt/selgrid/data /opt/selgrid/uploads

if [ -n "${GITHUB_TOKEN:-}" ]; then
  REPO_URL="https://${GITHUB_USERNAME:-x-access-token}:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
else
  REPO_URL="https://github.com/${GITHUB_REPOSITORY}.git"
fi

if [ -d /opt/selgrid/.git ]; then
  git -C /opt/selgrid fetch --depth 1 origin "${GITHUB_REF}"
  git -C /opt/selgrid checkout -f FETCH_HEAD
else
  rm -rf /opt/selgrid/*
  git clone --depth 1 --branch "${GITHUB_REF}" "${REPO_URL}" /opt/selgrid
fi

pip3 install --no-cache-dir -r /opt/selgrid/requirements.txt

exec supervisord -c /opt/selgrid/supervisord.conf
