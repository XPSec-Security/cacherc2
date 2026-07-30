#!/bin/bash

set -e

PROJECT_NAME="cacherc2"
VERSION=$(git describe --tags --always 2>/dev/null || echo "unknown")
TIMESTAMP=$(date +%s)

echo "Building $PROJECT_NAME"
echo "Version: $VERSION"
echo ""

# Create output directory
mkdir -p bin

# Build for Windows
echo "Building for Windows..."
GOOS=windows GOARCH=amd64 go build \
    -ldflags="-s -w -H windowsgui" \
    -o "bin/${PROJECT_NAME}-windows-x64.exe" \
    ./cmd/client

# Build for Linux
echo "Building for Linux..."
GOOS=linux GOARCH=amd64 go build \
    -ldflags="-s -w" \
    -o "bin/${PROJECT_NAME}-linux-x64" \
    ./cmd/client
chmod +x "bin/${PROJECT_NAME}-linux-x64"

# Build for macOS
echo "Building for macOS..."
GOOS=darwin GOARCH=amd64 go build \
    -ldflags="-s -w" \
    -o "bin/${PROJECT_NAME}-macos-x64" \
    ./cmd/client
chmod +x "bin/${PROJECT_NAME}-macos-x64"

# Build for macOS ARM64
echo "Building for macOS ARM64..."
GOOS=darwin GOARCH=arm64 go build \
    -ldflags="-s -w" \
    -o "bin/${PROJECT_NAME}-macos-arm64" \
    ./cmd/client
chmod +x "bin/${PROJECT_NAME}-macos-arm64"

echo ""
echo "Build completed successfully!"
echo ""
echo "Binaries:"
ls -lh bin/

echo ""
echo "Done!"
