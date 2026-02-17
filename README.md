# Mem0 REST API Server

A FastAPI-based REST server for managing memories with mem0, a memory management system for AI agents and applications. This server provides a simple HTTP interface for storing, retrieving, searching, and managing memories with support for multiple AI providers and vector databases.

## Features

- **Memory Management**: Create, read, update, and delete memories
- **Semantic Search**: Search memories using natural language queries
- **Multi-Provider Support**: Compatible with OpenAI and other LLM/embedding providers
- **Vector Store Options**: PostgreSQL/pgvector support for semantic similarity
- **User/Agent/Run Tracking**: Organize memories by user ID, agent ID, or run ID
- **Memory History**: Track changes to memories over time
- **Batch Operations**: Bulk memory creation and deletion
- **Health Checks**: Built-in monitoring endpoints

## Prerequisites

- Python 3.8+
- PostgreSQL 12+ (with pgvector extension) - optional, for vector storage
- OpenAI API key (or other supported LLM provider)

## Installation

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd mem0server
   ```

2. **Create and activate virtual environment**
   ```bash
   python -m venv mem
   source mem/bin/activate  # On Windows: mem\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

## Configuration

Configuration is managed through environment variables. Create a `.env` file or set these in your environment:

### Vector Store Configuration
```bash
MEM0_VECTOR_PROVIDER=pgvector          # Vector store provider (default: pgvector)
POSTGRES_HOST=localhost                 # PostgreSQL host (default: localhost)
POSTGRES_PORT=5432                      # PostgreSQL port (default: 5432)
POSTGRES_DB=postgres                    # Database name (default: postgres)
POSTGRES_USER=postgres                  # Database user (default: postgres)
POSTGRES_PASSWORD=postgres              # Database password (default: postgres)
POSTGRES_COLLECTION=mem0_memories       # Vector collection name (default: mem0_memories)
```

### LLM Configuration
```bash
MEM0_LLM_PROVIDER=openai                # LLM provider (default: openai)
MEM0_LLM_MODEL=gpt-5                    # Model name (default: gpt-5)
MEM0_LLM_TEMPERATURE=0.7                # Temperature parameter (optional)
OPENAI_API_KEY=sk-...                   # OpenAI API key (required for OpenAI provider)
MEM0_LLM_EXTRA_CONFIG='{"key":"value"}' # Additional LLM config as JSON
```

### Embedding Configuration
```bash
MEM0_EMBEDDER_PROVIDER=openai           # Embedder provider (default: openai)
MEM0_EMBEDDER_MODEL=text-embedding-3-small  # Embedding model (optional)
```

### Server Configuration
```bash
MEM0_HOST=127.0.0.1                     # Server host (default: 127.0.0.1)
MEM0_PORT=8000                          # Server port (default: 8000)
MEM0_WORKERS=1                          # Number of worker processes (default: 1)
MEM0_LOG_LEVEL=info                     # Logging level (default: info)
MEM0_HISTORY_DB_PATH=/var/lib/mem0/history.db  # History database path
```

## Running the Server

### Direct Execution
```bash
python server.py
```

### With Custom Configuration
```bash
export OPENAI_API_KEY=sk-...
export POSTGRES_HOST=db.example.com
export MEM0_PORT=8080
python server.py
```

### With Uvicorn
```bash
uvicorn server:app --host 0.0.0.0 --port 8000 --workers 4
```

The API will be available at `http://localhost:8000` with interactive documentation at `http://localhost:8000/docs`.

## API Endpoints

### Health & Documentation
- `GET /` - Redirects to OpenAPI documentation
- `GET /health` - Health check endpoint

### Memory Operations

#### Create Memories
```bash
POST /memories
Content-Type: application/json

{
  "messages": [
    {"role": "user", "content": "What's my name?"},
    {"role": "assistant", "content": "Your name is John"}
  ],
  "user_id": "user123",
  "metadata": {"source": "chat"}
}
```

#### Get All Memories
```bash
GET /memories?user_id=user123
```

#### Get Specific Memory
```bash
GET /memories/{memory_id}
```

#### Search Memories
```bash
POST /search
Content-Type: application/json

{
  "query": "What was discussed about John's project?",
  "user_id": "user123",
  "filters": {"source": "chat"}
}
```

#### Update Memory
```bash
PUT /memories/{memory_id}
Content-Type: application/json

{
  "updated_field": "new_value"
}
```

#### Get Memory History
```bash
GET /memories/{memory_id}/history
```

#### Delete Memory
```bash
DELETE /memories/{memory_id}
```

#### Delete All Memories
```bash
DELETE /memories?user_id=user123
```

#### Reset All Memories
```bash
POST /reset
```

#### Configure Server
```bash
POST /configure
Content-Type: application/json

{
  "version": "v1.1",
  "vector_store": {"provider": "pgvector", "config": {...}},
  "llm": {"provider": "openai", "config": {...}},
  "embedder": {"provider": "openai", "config": {...}}
}
```

## Request Models

### Message
```json
{
  "role": "user|assistant",
  "content": "message text"
}
```

### MemoryCreate
```json
{
  "messages": [Message],
  "user_id": "string (optional)",
  "agent_id": "string (optional)",
  "run_id": "string (optional)",
  "metadata": "object (optional)"
}
```

### SearchRequest
```json
{
  "query": "string (required)",
  "user_id": "string (optional)",
  "agent_id": "string (optional)",
  "run_id": "string (optional)",
  "filters": "object (optional)"
}
```

**Note**: At least one identifier (user_id, agent_id, or run_id) is required for memory retrieval and deletion operations.

## Docker Deployment

### Using Docker Compose
Create a `docker-compose.yml`:
```yaml
version: '3.8'

services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: postgres
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data

  mem0-server:
    build: .
    ports:
      - "8000:8000"
    environment:
      POSTGRES_HOST: postgres
      POSTGRES_PORT: 5432
      OPENAI_API_KEY: ${OPENAI_API_KEY}
      MEM0_PORT: 8000
      MEM0_HOST: 0.0.0.0
    depends_on:
      - postgres

volumes:
  postgres_data:
```

Run with:
```bash
docker-compose up
```

## Example Usage

### Python
```python
import requests

BASE_URL = "http://localhost:8000"

# Create a memory
response = requests.post(
    f"{BASE_URL}/memories",
    json={
        "messages": [
            {"role": "user", "content": "Remember this important fact"},
            {"role": "assistant", "content": "I've stored that for you"}
        ],
        "user_id": "user123"
    }
)
memory_id = response.json()["id"]

# Search memories
response = requests.post(
    f"{BASE_URL}/search",
    json={"query": "What did I tell you about important facts?", "user_id": "user123"}
)
results = response.json()

# Get memory by ID
response = requests.get(f"{BASE_URL}/memories/{memory_id}")
memory = response.json()

# Delete memory
requests.delete(f"{BASE_URL}/memories/{memory_id}")
```

### cURL
```bash
# Create memory
curl -X POST http://localhost:8000/memories \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {"role": "user", "content": "Store this"},
      {"role": "assistant", "content": "Stored"}
    ],
    "user_id": "user123"
  }'

# Search memories
curl -X POST http://localhost:8000/search \
  -H "Content-Type: application/json" \
  -d '{"query": "What was stored?", "user_id": "user123"}'

# Health check
curl http://localhost:8000/health
```

## Environment Variable Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `MEM0_VECTOR_PROVIDER` | `pgvector` | Vector store backend |
| `MEM0_LLM_PROVIDER` | `openai` | LLM provider |
| `MEM0_LLM_MODEL` | `gpt-5` | LLM model name |
| `MEM0_EMBEDDER_PROVIDER` | `openai` | Embedding provider |
| `MEM0_HOST` | `127.0.0.1` | Server host |
| `MEM0_PORT` | `8000` | Server port |
| `MEM0_WORKERS` | `1` | Worker processes |
| `MEM0_LOG_LEVEL` | `info` | Log level |
| `MEM0_HISTORY_DB_PATH` | `/var/lib/mem0/history.db` | History database location |
| `POSTGRES_HOST` | `localhost` | PostgreSQL host |
| `POSTGRES_PORT` | `5432` | PostgreSQL port |
| `POSTGRES_DB` | `postgres` | Database name |
| `POSTGRES_USER` | `postgres` | Database user |
| `POSTGRES_PASSWORD` | `postgres` | Database password |
| `POSTGRES_COLLECTION` | `mem0_memories` | Collection name |
| `OPENAI_API_KEY` | - | OpenAI API key (required) |

## Troubleshooting

### Memory instance failed to initialize
- Verify `OPENAI_API_KEY` is set correctly
- Check PostgreSQL connectivity: `POSTGRES_HOST`, `POSTGRES_PORT`, credentials
- Ensure pgvector extension is installed in PostgreSQL

### Search returning no results
- Verify memories were created with the correct user_id/agent_id/run_id
- Check that embeddings are being generated (requires valid OPENAI_API_KEY)
- Use `/memories` endpoint to confirm memories exist

### Connection timeout
- If using Docker, ensure services are on same network
- Check firewall rules for PostgreSQL port
- Verify environment variables match deployment configuration

## Performance Notes

- **Single worker mode** (`MEM0_WORKERS=1`) is suitable for development
- **Production deployments** should use 2-4 workers depending on load
- **Vector embeddings** are computed on memory creation; cache results where possible
- **Database indexes** on user_id/agent_id/run_id improve query performance

## License

Based on original source: https://code.m3ta.dev/m3tam3re/nixpkgs/src/branch/master/pkgs/mem0/server.py

## Support

For issues with mem0 library itself, see: https://github.com/mem0ai/mem0

## Changelog

### Version 1.0.0
- Initial release with core memory management features
- Support for OpenAI LLM and embedding providers
- PostgreSQL/pgvector vector store support
- RESTful API with comprehensive endpoints
- Health checks and monitoring
- Memory history tracking
