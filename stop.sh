#!/bin/bash

set -e

echo "🛑 Stopping mem0-server..."
docker-compose down -v

echo "✅ Services stopped and volumes removed."
echo ""
echo "To preserve data while stopping, run:"
echo "  docker-compose down"
