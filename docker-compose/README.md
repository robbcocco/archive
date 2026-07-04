# docker-compose

One directory per stack. Each contains a `compose.yml` and, when needed, a
`.env.example` template. Real `.env` files are gitignored; compose loads `.env`
automatically for `${VAR}` substitution.

## Usage

```sh
cd <stack>
cp .env.example .env
# fill in any `changeme` values in .env
docker compose up -d
```

Extra files in some stacks:

- `media-stack/utils.yml` — prowlarr, bazarr, unmanic, tagr, flaresolverr; shares the
  stack's `.env`: `docker compose -f utils.yml up -d`
- `portainer/agent.yml` — agent for secondary hosts: `docker compose -f agent.yml up -d`

## Stacks

| Stack | Services |
|---|---|
| adguard | AdGuard Home |
| affine | AFFiNE, postgres, redis |
| beeper | Beeper telegram bridge |
| calibre | Calibre-Web Automated, book downloader, flare-bypasser |
| cups | CUPS print server (local build: Dockerfile, cupsd.conf, printer presets) |
| homarr | Homarr dashboard |
| homebridge | Homebridge |
| invidious | Invidious, companion, postgres |
| jellyfin | Jellyfin |
| komga | Komga |
| media-stack | radarr, sonarr, lidarr, mylar3, qbittorrent (+ utils.yml) |
| navidrome | Navidrome, Feishin |
| owncloud | ownCloud, mariadb, redis |
| plex | Plex |
| portainer | Portainer CE (+ agent.yml) |
| watchtower | Watchtower with telegram notifications |
