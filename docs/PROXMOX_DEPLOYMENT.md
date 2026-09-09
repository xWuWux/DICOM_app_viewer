# Deploying Orthanc + viewer on a separate Proxmox VM/LXC

This is an alternative to the same-Docker-host setup in the main `README.md`
(where Kasm Workspaces and the `orthanc`/`viewer` containers all run on one
machine, using Kasm's own `kasm_default_network` for container-name
reachability). Use this instead when Kasm runs on one host and you want
Orthanc/the viewer on their own Proxmox-hosted VM or LXC container.

**Scope**: this is deployment config + documentation, not automation. You
provision the VM/LXC yourself (in the Proxmox web UI, or however you
normally do it) — nothing here talks to the Proxmox API.

## 1. Provision the VM/LXC

Nothing exotic: a Debian/Ubuntu VM or LXC container with Docker (and the
`docker compose` plugin) installed, reachable over the network from wherever
Kasm Workspaces runs. For the two lightweight containers this repo runs
(nginx + Orthanc, no heavy DICOM processing at this MVP's scale), 2 vCPU /
4GB RAM / 20GB disk is a reasonable starting point — scale up once you know
your real study volume and concurrent-session count.

## 2. Deploy this repo onto it

```bash
git clone <this repo> && cd DICOM_app_viewer
cp .env.example .env && sed -i "s/^ORTHANC_PASSWORD=.*/ORTHANC_PASSWORD=$(openssl rand -hex 16)/" .env
docker compose -f docker-compose.remote-host.yml up -d --build
./scripts/fetch-public-samples.sh
ORTHANC_URL=http://localhost:8042 ./scripts/load-sample-studies.sh
```

`docker-compose.remote-host.yml` is a standalone file (not layered on top of
`docker-compose.yml` — see the comment at its top for why), differing from
the same-host setup in two ways: no `kasm_default_network` attachment (that
network doesn't exist here — Kasm isn't on this host), and Orthanc's `8042`
plus the viewer's `8043` bind to `BIND_ADDR` (default `0.0.0.0`) instead of
`127.0.0.1`, since Kasm now reaches them over the real network.

## 3. Firewall — not optional

The same-host setup gets its isolation for free: `kasm_default_network` is a
Docker-internal bridge nothing outside that host can reach. This setup has no
equivalent, so **you must firewall this VM/LXC's `8042` and `8043` to only
your Kasm host's IP** — without it, `8042` exposes Orthanc's Basic-Auth UI to
anyone who can reach this host at all, and `8043` (the auth-injecting proxy —
see `docker/viewer/default.conf.template`) hands out valid, unauthenticated
Orthanc access to anyone who reaches it, no login required, by design (that's
what makes it work transparently from inside an iframe).

Using Proxmox's own firewall (Datacenter → Firewall, or the VM/LXC's own
Firewall tab): add an **IN, ACCEPT** rule with **Source** = your Kasm host's
IP and **Dest. port** = `8042,8043`, then a final **IN, DROP/REJECT** rule
for those ports from everywhere else (Proxmox evaluates rules in order,
first match wins).

Without Proxmox's firewall enabled on the guest, `ufw` works the same way:

```bash
sudo ufw allow from <KASM_HOST_IP> to any port 8042,8043 proto tcp
sudo ufw deny 8042,8043/tcp
```

Port `8080` (the watermarked wrapper page itself) is meant to be reachable
from wherever Kasm's session containers render it — same as the same-host
setup, this is the one port that's supposed to be open.

## 4. Point the Kasm workspace at this host

Everything on the Kasm side is otherwise unchanged from the main README —
build the workspace image, register it, generate an API key, configure DLP
group settings. The one difference is the image build needs to know this
VM's address instead of the same-host default:

```bash
docker build --build-arg REMOTE_HOST=<this VM's hostname or IP> \
  -t ipcmc/dicom-viewer:mvp docker/kasm-workspace
```

(Or skip the rebuild and set `VIEWER_URL`/`ORTHANC_URL` directly in the
Kasm admin UI's **Docker Run Config Override** for the registered workspace
— same effect, no image rebuild needed. See `docker/kasm-workspace/Dockerfile`
for exactly what these control.)

## What's unchanged

Kasm Workspaces installation, the workspace image itself, DLP group
settings, `scripts/create-session.py`, the watermark — none of that cares
where Orthanc/viewer physically run. This document only covers the pieces
that actually differ.
