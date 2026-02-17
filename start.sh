#!/bin/bash

set -e

echo "🚀 Starting mem0-server..."

if [ ! -f .env ]; then
    echo "⚠️  .env file not found. Copying from .env.example..."
    cp .env.example .env
    echo "📝 Please edit .env and set your OPENAI_API_KEY before running again."
    exit 1
fi

echo "📊 Creating data directories..."
mkdir -p data/postgres data/mem0

echo "🐳 Starting Docker services..."
docker-compose up -d --build

echo ""
echo "✅ Services started!"
echo ""
echo "📋 Service Status:"
docker-compose ps
echo ""
echo "🔗 API Access:"
echo "   Base URL: http://localhost:$(grep MEM0_PORT .env | cut -d= -f2 || echo 8000)"
echo "   Docs:     http://localhost:$(grep MEM0_PORT .env | cut -d= -f2 || echo 8000)/docs"
echo "   Health:   http://localhost:$(grep MEM0_PORT .env | cut -d= -f2 || echo 8000)/health"
echo ""
echo "📜 View logs:"
echo "   docker-compose logs -f mem0-server"
echo ""
echo "🛑 Stop services:"
echo "   docker-compose down"
