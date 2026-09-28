# RIP test deployment

The RIP (Requirement Intelligence Platform) deployment that runs on
10.16.16.8 in `/home/bjit/rip`, plus the OTel monitor that AMS pulls RIP
logs from.

| Service | Port |
|---|---|
| Frontend | 7901 |
| API | 7900 (`/docs`) |
| Monitor | 7788 |

Postgres, Neo4j and Redis are not published on the host.

## Layout

- `backend/`, `frontend/`: copies of the RIP backend and frontend repos.
- `docker-compose.yml`: includes `backend/docker-compose.yml` and adds the
  frontend, `rip-monitor` and `rip-otel-collector`.
- `rip-ports.override.yml`: the server ports, and no host ports for the data stores.
- `rip-logging.override.yml`: writes each container's Compose project and
  service into its Docker log lines, so the collector can find RIP logs.
- `otel-collector/config.yaml`: reads the RIP container logs and sends
  WARNING and worse to the monitor.
- `otel-monitor/`: the progCoder `monitor.py`, run in Docker.

## Deploy

1. Copy `backend/.env.example` to `backend/.env` and fill in the real values.
   For plain HTTP on an IP address, also set:
   - `CORS_ORIGINS`: add `http://<host>:7901`.
   - `FRONTEND_BASE_URL=http://<host>:7901`
   - `COOKIE_SAMESITE=lax` and `COOKIE_SECURE=false`. A browser never stores
     a Secure cookie over plain HTTP.
2. Copy `.env.example` to `.env` and set `RIP_MONITOR_API_KEY`.
3. If the host is not 10.16.16.8, change `VITE_API_BASE_URL` in
   `docker-compose.yml`. Vite puts it into the bundle at build time.
4. Start:

   ```bash
   docker compose up -d --build
   ```

A change inside `backend/.env` does not always make Compose recreate a
container. After you edit it, run `docker compose up -d --force-recreate`.

## AMS connector

In the AMS customer dashboard, add **Custom Monitor API (Pull)** with the base
URL `http://<host>:7788` and the `RIP_MONITOR_API_KEY` value as the API token.
