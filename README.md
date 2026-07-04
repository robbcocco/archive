# archive

Personal homelab archive: docker compose stacks, host setup notes, and misc configs.

## Layout

| Directory | Contents |
|---|---|
| [docker-compose/](docker-compose/) | One directory per stack, each with `compose.yml` + `.env` |
| [proxmox/](proxmox/) | Proxmox host setup notes |
| [openapi/](openapi/) | OpenAPI specs (Google Calendar, Google Tasks) |
| [misc/](misc/) | Everything else |

## Secrets

No real credentials are committed. `.env` files contain placeholders (`changeme`);
set real values on the target host and keep them out of git.
