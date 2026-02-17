#!/bin/bash

set -e

echo "🐋 Building mem0-server Docker image..."
docker-compose build --no-cache mem0-server

echo "✅ Build complete!"
echo ""
echo "To start the services, run:"
echo "  docker-compose up -d"
echo ""
echo "To view logs:"
echo "  docker-compose logs -f mem0-server"
echo ""
echo "To stop the services:"
echo "  docker-compose down"
