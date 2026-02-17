# Quick Start Guide

## 30-Second Setup

```bash
# 1. Copy configuration template
cp .env.example .env

# 2. Edit .env - add your OpenAI API key
# OPENAI_API_KEY=sk-...

# 3. Build and start
bash build.sh
bash start.sh

# 4. Check status
bash status.sh

# 5. Access at http://localhost:8000/docs
```

## Key Feature: Hot-Reloadable .env

The `.env` file is **mounted as a read-only volume** in the docker-compose setup. This means:

✅ Edit `.env` without rebuilding  
✅ Changes apply on service restart  
✅ Sensitive data stays outside containers  
✅ Easy local development workflow  

**Update configuration and restart:**
```bash
# Edit .env
nano .env

# Restart services (optional - OPENAI_API_KEY can be updated on restart)
docker-compose restart mem0-server
```

## Common Commands

| Task | Command |
|------|---------|
| **Start services** | `bash start.sh` or `docker-compose up -d` |
| **Stop services** | `bash stop.sh` or `docker-compose down` |
| **View status** | `bash status.sh` or `docker-compose ps` |
| **View logs** | `docker-compose logs -f mem0-server` |
| **Check health** | `curl http://localhost:8000/health` |
| **Access API docs** | Open http://localhost:8000/docs |
| **Rebuild image** | `bash build.sh` or `docker-compose build --no-cache` |

## Architecture

```
┌─────────────────────────────────────────────────┐
│              Docker Compose Stack               │
├─────────────────────────────────────────────────┤
│                                                 │
│  ┌─────────────────┐        ┌────────────────┐ │
│  │  mem0-server    │◄──────►│   PostgreSQL   │ │
│  │  (FastAPI)      │        │  + pgvector    │ │
│  │  Port: 8000     │        │  Port: 5432    │ │
│  └─────────────────┘        └────────────────┘ │
│        ▲                            ▲           │
│        │                            │           │
│   HTTP │                   Volumes  │           │
│   /:docs/:api                      │           │
│        │                    /data/postgres     │
│        │                    /data/mem0         │
│        └─────────────────────────────────────┘ │
│                                                 │
│   Network: mem0-network (172.20.0.0/16)       │
└─────────────────────────────────────────────────┘
```

## File Breakdown

| File | Purpose |
|------|---------|
| `Dockerfile` | Multi-stage build for mem0-server |
| `docker-compose.yaml` | Service orchestration (PostgreSQL + mem0) |
| `.env.example` | Configuration template |
| `.dockerignore` | Docker build optimization |
| `build.sh` | Build script |
| `start.sh` | Start services with setup |
| `stop.sh` | Stop and clean services |
| `status.sh` | Quick status check |
| `DEPLOYMENT.md` | Full deployment documentation |

## Default Ports

| Service | Port | URL |
|---------|------|-----|
| **mem0-server** | 8000 | http://localhost:8000 |
| **PostgreSQL** | 5432 | postgres://localhost:5432 |

## Environment Variables

### Required
- `OPENAI_API_KEY` - Your OpenAI API key

### Common Customizations
```bash
MEM0_PORT=8000                    # Change API port
MEM0_WORKERS=4                    # CPU/memory allocation
MEM0_LOG_LEVEL=debug              # More verbose logging
POSTGRES_PASSWORD=securepass      # Change DB password
```

See `.env.example` for all available options.

## Troubleshooting

### Services won't start
```bash
# Check Docker is running
docker ps

# View error logs
docker-compose logs mem0-server

# Rebuild from scratch
docker-compose down -v
docker-compose build --no-cache
docker-compose up -d
```

### Port already in use
```bash
# Find what's using port 8000
lsof -i :8000

# Change in .env
MEM0_PORT=8001
docker-compose up -d
```

### API not responding
```bash
# Check if container is running
docker-compose ps

# View recent logs
docker-compose logs --tail=50 mem0-server

# Test health endpoint
curl -v http://localhost:8000/health
```

### Database connection errors
```bash
# Check PostgreSQL is healthy
docker-compose ps postgres

# Test database connection
docker-compose exec postgres psql -U postgres -d postgres -c "SELECT 1"

# View PostgreSQL logs
docker-compose logs postgres
```

## Performance Tips

1. **Scale workers** for concurrent requests:
   ```bash
   MEM0_WORKERS=8  # In .env
   ```

2. **Monitor resource usage**:
   ```bash
   docker stats mem0-server mem0-postgres
   ```

3. **Check database connections**:
   ```bash
   docker-compose exec postgres psql -U postgres -d postgres -c "\l"
   ```

## Security Checklist

- [ ] OPENAI_API_KEY is set in `.env`
- [ ] `.env` file has restrictive permissions: `chmod 600 .env`
- [ ] PostgreSQL password is changed from default
- [ ] Firewall rules restrict database port (5432)
- [ ] API is behind a reverse proxy in production
- [ ] SSL/TLS enabled for external access
- [ ] Regular backups of data volumes

## Next Steps

1. **Read full docs**: See [DEPLOYMENT.md](DEPLOYMENT.md)
2. **Test API**: Visit http://localhost:8000/docs
3. **Create first memory**: Use the Swagger UI or curl
4. **Check logs**: `docker-compose logs -f`
5. **Monitor usage**: `docker stats`

## Support

- **mem0 docs**: https://docs.mem0.ai/
- **FastAPI docs**: https://fastapi.tiangolo.com/
- **PostgreSQL docs**: https://www.postgresql.org/docs/
- **Docker docs**: https://docs.docker.com/
