#!/bin/bash

set -e

echo "📊 Getting mem0-server status..."
docker-compose ps

echo ""
echo "📝 Recent logs:"
docker-compose logs --tail=20 mem0-server

echo ""
echo "🔗 Quick access links:"
echo "   API Docs:  http://localhost:8000/docs"
echo "   Health:    http://localhost:8000/health"
echo ""
echo "💾 Database info:"
echo "   Host:      postgres"
echo "   Port:      5432"
echo "   Database:  $(grep POSTGRES_DB .env 2>/dev/null | cut -d= -f2 || echo 'postgres')"
echo ""
echo "View full logs:"
echo "  docker-compose logs -f mem0-server"
