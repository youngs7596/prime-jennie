#!/usr/bin/env bash
# prime-jennie systemd autostart script.
# Starts infra profile first, waits for health, then starts real profile.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

echo "[systemd-autostart] Checking for NVIDIA GPU readiness..."
for ((i=1; i<=60; i++)); do
    if nvidia-smi > /dev/null 2>&1; then
        echo "[systemd-autostart] GPU detected successfully."
        break
    fi
    echo "[systemd-autostart] GPU not ready yet (attempt $i/60). Waiting 1s..."
    sleep 1
done

echo "[systemd-autostart] Starting infra profile..."
/usr/bin/docker compose --profile infra up -d

echo "[systemd-autostart] Waiting for infra services to be healthy..."
for service in redis mariadb; do
    echo "[systemd-autostart] Checking $service health..."
    timeout 120 bash -c "until docker compose ps $service | grep -q 'healthy'; do sleep 5; done" || {
        echo "[systemd-autostart] WARNING: $service health check timed out, continuing..."
    }
done

echo "[systemd-autostart] Starting real profile..."
/usr/bin/docker compose --profile real up -d

echo "[systemd-autostart] All profiles started successfully!"
