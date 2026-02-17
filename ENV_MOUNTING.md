# Environment File Mounting Guide

## Overview

The docker-compose.yaml is configured to load environment variables from a local `.env` file using two complementary methods:

### 1. `env_file` Directive (Primary)
```yaml
mem0-server:
  env_file:
    - .env
```
This loads all variables from `.env` into the container's environment at startup.

### 2. Volume Mount (Fallback)
```yaml
volumes:
  - .env:/app/.env:ro
```
This mounts the `.env` file as read-only inside the container at `/app/.env`, allowing the application to read it directly if needed.

## Benefits

✅ **No rebuild needed** - Update .env and restart containers  
✅ **Security** - Keep API keys outside Docker images  
✅ **Flexibility** - Use different .env files for dev/staging/prod  
✅ **Simplicity** - Single source of truth for configuration  
✅ **Hot updates** - Some variables can be changed on-the-fly  

## Workflow

### Initial Setup
```bash
# Create .env from template
cp .env.example .env

# Edit with your configuration
nano .env

# Start services
docker-compose up -d
```

### Updating Configuration

**For non-critical settings:**
```bash
# Edit .env
nano .env

# Services pick up changes on restart
docker-compose restart mem0-server
```

**For sensitive settings (API keys):**
```bash
# Edit .env
nano .env

# Full restart to ensure consistency
docker-compose down
docker-compose up -d
```

## File Structure

```
mem0server/
├── .env                  ← Your local configuration (git-ignored)
├── .env.example         ← Template with defaults
├── docker-compose.yaml  ← References .env
├── Dockerfile
├── server.py
└── requirements.txt
```

## Important Notes

### `.env` is Git-Ignored
The `.env` file contains sensitive data and is not tracked by Git:
```bash
# In .gitignore
.env
```

Keep `.env.example` in Git for reference only.

### Mounted as Read-Only
The volume mount is read-only for safety:
```yaml
volumes:
  - .env:/app/.env:ro  # :ro = read-only
```

The container cannot modify the original .env file.

### Environment Variable Precedence

Variables are loaded in this order (later overrides earlier):

1. Docker Compose defaults (in docker-compose.yaml)
2. `.env` file values (via `env_file`)
3. `-e` flags in `docker-compose up`
4. Container environment section in docker-compose.yaml

Example:
```yaml
# docker-compose.yaml
env_file:
  - .env
environment:
  MEM0_PORT: 8000  # This overrides .env value
```

## Examples

### Changing Database Password

```bash
# Edit .env
echo "POSTGRES_PASSWORD=your-new-password" >> .env

# Restart
docker-compose down -v
docker-compose up -d
```

### Switching LLM Provider

```bash
# Edit .env
nano .env
# Change: MEM0_LLM_PROVIDER=anthropic

# Restart
docker-compose restart mem0-server
```

### Increasing Worker Processes

```bash
# Edit .env
sed -i 's/MEM0_WORKERS=4/MEM0_WORKERS=8/' .env

# Restart
docker-compose restart mem0-server

# Verify
docker-compose logs mem0-server | grep -i worker
```

## Troubleshooting

### Changes not taking effect

Check if .env file exists and is readable:
```bash
ls -la .env
cat .env | head
```

Verify docker-compose loaded it:
```bash
docker-compose config | grep POSTGRES_PASSWORD
```

### Permission denied on .env

Fix permissions:
```bash
chmod 644 .env
```

### Variables not in container

Verify they were loaded:
```bash
docker-compose exec mem0-server env | grep MEM0
```

If not present, restart with fresh config:
```bash
docker-compose down
docker-compose up -d
```

## Best Practices

1. **Never commit .env to Git**
   ```bash
   # Verify in .gitignore
   cat .gitignore | grep "^.env"
   ```

2. **Keep .env.example up-to-date**
   ```bash
   # When adding new variables
   echo "NEW_VAR=default_value" >> .env.example
   ```

3. **Use strong passwords**
   ```bash
   # For POSTGRES_PASSWORD
   openssl rand -base64 32
   ```

4. **Backup .env securely**
   ```bash
   # Before deploying
   cp .env .env.backup
   chmod 600 .env.backup
   ```

5. **Document required variables**
   Create a `CONFIGURATION.md`:
   ```markdown
   # Required Configuration

   - OPENAI_API_KEY: Your OpenAI API key
   - POSTGRES_PASSWORD: Database password
   ```

## Integration with CI/CD

### GitHub Actions Example
```yaml
- name: Deploy with custom env
  run: |
    echo "OPENAI_API_KEY=${{ secrets.OPENAI_API_KEY }}" > .env
    echo "POSTGRES_PASSWORD=${{ secrets.DB_PASSWORD }}" >> .env
    docker-compose up -d
```

### GitLab CI Example
```yaml
deploy:
  script:
    - cp .env.example .env
    - echo "OPENAI_API_KEY=$OPENAI_API_KEY" >> .env
    - docker-compose up -d
```
