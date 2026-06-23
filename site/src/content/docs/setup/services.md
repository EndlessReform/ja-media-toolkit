---
title: Setup Services
---

This project uses a Docker-based service stack to provide API access to anime metadata and subtitles. The "front door" is a Caddy-powered documentation site that also acts as a reverse proxy for these backend services.

## Prerequisites

Before starting the services, ensure you have the following installed:

- **Docker Engine**: A compatible Docker runtime (e.g., Docker Desktop, OrbStack).
- **Docker Compose**: Installed as part of your Docker suite.

## Configuration

The services are coordinated via `compose.yaml`. While most configuration is baked into the compose file, you can override certain behaviors using environment variables in a `.env` file at the project root.

### Key Environment Variables

| Variable | Description | Default |
| :--- | :--- | :--- |
| `DOCS_PORT` | The port on which the documentation and API gateway are exposed. | `8080` |
| `ANIME_AUDIO_LIBRARY_PATH` | Host directory containing derived anime-audio series directories. | `./data/anime-audio` |

*Note: Set `DOCS_PORT=80` in production for standard HTTP access.*

## Startup

To build and start the entire stack in the background:

```bash
docker compose up -d --build
```

### Verifying Installation

Once the containers are healthy, you can verify the services are running:

1. **Documentation**: Visit `http://localhost:${DOCS_PORT:-8080}` to view the project documentation.
2. **API Gateway**: The backend services are available via the following API paths:
    - **Anime Crosswalk**: `http://localhost:${DOCS_PORT:-8080}/api/v1/crosswalk`
    - **Kitsunekko Subtitles**: `http://localhost:${DOCS_PORT:-8080}/api/v1/subtitles`
    - **Anime Audio**: `http://localhost:${DOCS_PORT:-8080}/api/v1/audio`

## Management

### Viewing Logs
To see the logs for all services:
```bash
docker compose logs -f
```

### Stopping Services
To stop the services without removing the volumes:
```bash
docker compose down
```
