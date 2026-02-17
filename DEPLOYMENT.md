# Deployment Guide

Complete guide for deploying mem0-server using Docker and Docker Compose.

## Quick Start

### 1. Prerequisites

- Docker 20.10+
- Docker Compose 1.29+
- OpenAI API key

### 2. Initial Setup

```bash
cp .env.example .env
```

Edit `.env` and set your `OPENAI_API_KEY`:
```bash
OPENAI_API_KEY=sk-your-key-here
```

### 3. Build & Start

```bash
bash build.sh
bash start.sh
```

Or manually:
```bash
docker-compose build
docker-compose up -d
```

### 4. Verify Deployment

```bash
bash status.sh

# Or check manually
curl http://localhost:8000/health
```

## Available Scripts

### `build.sh` - Build the Docker image
```bash
bash build.sh
```
Builds the mem0-server Docker image with no-cache flag.

### `start.sh` - Start all services
```bash
bash start.sh
```
- Ensures .env exists
- Creates data directories
- Starts PostgreSQL and mem0-server
- Shows access information

### `stop.sh` - Stop all services
```bash
bash stop.sh
```
Stops all services and removes volumes (data will be deleted).

To stop without removing data:
```bash
docker-compose down
```

### `status.sh` - Check service status
```bash
bash status.sh
```
Shows running services, recent logs, and access links.

## Manual Docker Compose Commands

### Start services
```bash
docker-compose up -d
```

### View logs
```bash
docker-compose logs -f mem0-server      # Follow mem0-server logs
docker-compose logs -f postgres         # Follow PostgreSQL logs
docker-compose logs                     # View all logs
```

### Stop services (preserve data)
```bash
docker-compose down
```

### Stop services (remove data)
```bash
docker-compose down -v
```

### Rebuild services
```bash
docker-compose build --no-cache
```

### Access PostgreSQL
```bash
docker-compose exec postgres psql -U postgres -d postgres
```

### View resource usage
```bash
docker stats
```

## Environment Configuration

### Loading Environment Variables

The docker-compose.yaml automatically loads environment variables from `.env` file in two ways:

1. **`env_file` directive** - Loads all variables into the mem0-server container
   ```yaml
   env_file:
     - .env
   ```

2. **Volume mount** - Also mounts .env as read-only file inside container
   ```yaml
   volumes:
     - .env:/app/.env:ro
   ```

This means you can:
- Edit `.env` locally and changes are reflected without rebuilding
- Keep sensitive data outside the container
- Use the same .env file across multiple environments

### Making Changes to .env

After editing `.env`:

```bash
# Option 1: Restart only affected services
docker-compose up -d postgres mem0-server

# Option 2: Full restart (cleaner)
docker-compose down
docker-compose up -d
```

The changes take effect immediately on restart.

### Edit `.env` to customize:

### Server Settings
```bash
MEM0_PORT=8000              # API port
MEM0_WORKERS=4              # Number of worker processes
MEM0_LOG_LEVEL=info         # Log level
MEM0_WORKERS=4              # Concurrency level
```

### Database Settings
```bash
POSTGRES_HOST=postgres      # Database host
POSTGRES_PORT=5432          # Database port
POSTGRES_DB=postgres        # Database name
POSTGRES_USER=postgres      # Database user
POSTGRES_PASSWORD=postgres  # Database password
```

### LLM Settings
```bash
MEM0_LLM_PROVIDER=openai           # LLM provider
MEM0_LLM_MODEL=gpt-4               # Model to use
MEM0_LLM_TEMPERATURE=0.7           # Temperature parameter
OPENAI_API_KEY=sk-...              # API key (REQUIRED)
MEM0_LLM_EXTRA_CONFIG='...'        # Extra LLM config as JSON
```

### Embedding Settings
```bash
MEM0_EMBEDDER_PROVIDER=openai              # Embedder provider
MEM0_EMBEDDER_MODEL=text-embedding-3-small # Embedding model
```

## Monitoring

### Check service health
```bash
curl http://localhost:8000/health
```

Response:
```json
{"status": "healthy", "service": "mem0-api"}
```

### View Docker resource usage
```bash
docker stats mem0-server mem0-postgres
```

### Check logs for errors
```bash
docker-compose logs mem0-server | grep -i error
```

## Troubleshooting

### PostgreSQL connection errors
```bash
# Check if PostgreSQL is running
docker-compose ps postgres

# Check PostgreSQL logs
docker-compose logs postgres

# Test connection
docker-compose exec postgres psql -U postgres -d postgres -c "SELECT 1"
```

### mem0-server won't start
```bash
# Check logs
docker-compose logs mem0-server

# Check if port is available
lsof -i :8000

# Rebuild with fresh dependencies
docker-compose build --no-cache mem0-server
docker-compose up -d
```

### Out of memory errors
Reduce `MEM0_WORKERS` in `.env`:
```bash
MEM0_WORKERS=2
```

Then restart:
```bash
docker-compose down
docker-compose up -d
```

### Database connection timeouts
- Ensure PostgreSQL is healthy: `docker-compose ps`
- Check network connectivity: `docker network inspect mem0-network`
- Increase startup grace period in docker-compose.yaml

## Performance Tuning

### Increase worker processes
```bash
MEM0_WORKERS=8  # For high-load environments
```

### PostgreSQL optimization
Edit docker-compose.yaml:
```yaml
POSTGRES_INITDB_ARGS: "-c shared_buffers=512MB -c max_connections=300"
```

### Enable query caching
Add to `.env`:
```bash
MEM0_CACHE_ENABLED=true
MEM0_CACHE_TTL=3600
```

## Data Backup

### Backup PostgreSQL database
```bash
docker-compose exec postgres pg_dump -U postgres postgres > backup.sql
```

### Backup volumes
```bash
docker run --rm -v mem0-postgres:/data -v $(pwd):/backup \
  alpine tar czf /backup/postgres_backup.tar.gz /data

docker run --rm -v mem0-history:/data -v $(pwd):/backup \
  alpine tar czf /backup/mem0_backup.tar.gz /data
```

### Restore PostgreSQL database
```bash
docker-compose exec -T postgres psql -U postgres postgres < backup.sql
```

## Production Deployment

### Security

1. Change default PostgreSQL password:
   ```bash
   POSTGRES_PASSWORD=your-secure-password
   ```

2. Use environment file instead of .env in repo:
   ```bash
   docker-compose --env-file /path/to/secure/.env up -d
   ```

3. Set restrictive file permissions:
   ```bash
   chmod 600 .env
   chmod 755 *.sh
   ```

4. Use Docker secrets (if using Swarm):
   ```bash
   docker secret create openai_key -
   # Then reference in compose file
   ```

### Scaling

For multiple instances behind a load balancer:

```yaml
mem0-server:
  deploy:
    replicas: 3
  # ... rest of config
```

### Logging

Send logs to external service:

```yaml
logging:
  driver: "splunk"
  options:
    splunk-token: "${SPLUNK_TOKEN}"
    splunk-url: "${SPLUNK_URL}"
```

## Useful Links

- [Docker Documentation](https://docs.docker.com/)
- [Docker Compose Reference](https://docs.docker.com/compose/compose-file/)
- [PostgreSQL Docker](https://hub.docker.com/_/postgres)
- [pgvector GitHub](https://github.com/pgvector/pgvector)
- [mem0 Documentation](https://docs.mem0.ai/)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
