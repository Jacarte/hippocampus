#!/bin/bash

set -e

# Read version from VERSION file (source of truth for image tags)
VERSION=$(cat VERSION)

echo "🐋 Building mem0-server Docker image (version: ${VERSION})..."
docker-compose build --no-cache \
  --build-arg VERSION="${VERSION}" \
  mem0-server

echo "✅ Build complete! Image tagged as mem0-server:${VERSION}"
echo ""
echo "To start the services, run:"
echo "  docker-compose up -d"
echo ""
echo "To view logs:"
echo "  docker-compose logs -f mem0-server"
echo ""
echo "To stop the services:"
echo "  docker-compose down"
