#!/bin/bash
# Build dockerizado y reproducible del APK de RomLibraryClient.
# Requiere: ~/android-sdk (SDK ya provisionado) y ~/gradle-dist/gradle-8.7
# ya presentes en el host (jarjar), montados de solo lectura en el contenedor.
set -euo pipefail
cd "$(dirname "$0")"

sudo docker build -t romlibrary-builder ./docker

sudo docker run --rm \
  -v "$(pwd)":/workspace \
  -v "$HOME/android-sdk":/opt/android-sdk:ro \
  -v "$HOME/gradle-dist/gradle-8.7":/opt/gradle:ro \
  -v romlibrary-gradle-cache:/root/.gradle \
  -w /workspace \
  romlibrary-builder \
  gradle -q :app:assembleDebug

echo "APK generada en app/build/outputs/apk/debug/app-debug.apk"
