---
title: Audiobookshelf
---

Audiobookshelf provides the playback surface for the derived anime audio
library. It runs as a separate Compose project from the repository-owned
service stack: Audiobookshelf owns playback progress and downloads, while
ja-media manifests remain authoritative for source identity and conversion
history.

The deployment pins Audiobookshelf `2.35.1`. That version and its multi-platform
container image were verified against the official GitHub release and GHCR
registry on June 22, 2026.

## Storage layout

The default deployment uses these host paths:

| Host path | Container path | Purpose |
| :--- | :--- | :--- |
| `deploy/audiobookshelf/config` | `/config` | Audiobookshelf database and configuration |
| `deploy/audiobookshelf/metadata` | `/metadata` | Cached covers, downloads, and other application metadata |
| `/mnt/magi06/media/derived-audio` | `/audio` | Read-only generated anime audio library |

Keep `config` and `metadata` on storage local to the Docker VM. Do not place
Audiobookshelf's SQLite-backed configuration on the `/mnt/magi06` network
mount.

The media path combines the host bind mount root, `/mnt/magi06`, with the
derived-audio folder root, `media/derived-audio`.

## Prepare the deployment

On the Docker host, enter the deployment directory and create the local state
directories:

```bash
cd deploy/audiobookshelf
mkdir -p config metadata
cp .env.example .env
```

Confirm that the derived library is mounted and readable:

```bash
test -d /mnt/magi06/media/derived-audio
find /mnt/magi06/media/derived-audio -maxdepth 2 -type f | head
```

The defaults in `.env.example` should work as-is. Change
`AUDIOBOOKSHELF_PORT` if port `13378` is already occupied. Change the two local
state paths only if the replacement paths are still on local VM storage.

## Start Audiobookshelf

Pull the pinned image and start the separate Compose project:

```bash
docker compose pull
docker compose up -d
```

Verify the container:

```bash
docker compose ps
docker compose logs --tail=100 audiobookshelf
```

Open `http://<docker-host>:13378` and create the initial administrator account.

## Create the anime audio library

In the Audiobookshelf web interface:

1. Open **Settings → Libraries**.
2. Add a new library with media type **Podcasts**.
3. Give it a name such as **Anime Audio**.
4. Add `/audio` as its only folder.
5. Save the library and run a scan.

Use a podcast library rather than an audiobook library. Each generated episode
then keeps independent playback progress, and newly generated episodes append
without changing a single series-wide timeline.

The media mount is deliberately read-only. Metadata edits made in
Audiobookshelf affect its local application data; generated audio and
repository-owned manifests remain untouched.

## Upgrade

Check the
[official Audiobookshelf releases](https://github.com/advplyr/audiobookshelf/releases)
and update `AUDIOBOOKSHELF_VERSION` in `.env` deliberately. Then run:

```bash
docker compose pull
docker compose up -d
```

Avoid switching the deployment to the moving `latest` or `edge` tags. A pinned
version makes upgrades and rollback decisions explicit.

## Stop the service

```bash
docker compose down
```

This removes the container and Compose network but preserves the bind-mounted
configuration, metadata, and media library.
