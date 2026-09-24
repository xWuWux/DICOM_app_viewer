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

## 5. Transport security — read this before any real or public deployment

Everything above (steps 1-4) gets you a working separate-host setup. It
does **not**, by itself, get you an encrypted one. This section exists
because that distinction matters a lot once real DICOM data and real
radiologists (reachable "from anywhere on earth" per this project's actual
plan for the live stage, not just an internal pilot) are involved — see
issue #54.

### 5a. What's encrypted today, and what isn't

- **Radiologist's browser ↔ Kasm's own gateway**: HTTPS, but Kasm installs
  with a **self-signed** certificate by default (`docs/LOCAL_SETUP_GUIDE.md`
  confirms this, and every `create-session.py` invocation documented so far
  uses `--insecure` specifically to bypass it). A self-signed cert that
  nothing validates is one MITM away from meaningless — fine for local dev,
  **not acceptable once this is reachable from outside a network you fully
  control.** See 5b.
- **Kasm host ↔ this repo's own ports** (`8042`/`8043`/`8080`, whether the
  same-Docker-host setup or this document's separate-host one): **plain
  HTTP**, always. On the same-host setup this never leaves the Docker
  bridge, so it's as safe as any same-host IPC. On *this* document's
  separate-host setup, it crosses a real network link between two physical
  hosts, and until now the only documented protection was firewall
  IP-allowlisting (step 3 above) — not encryption. If that link can ever
  cross anything other than a fully private, physically-controlled segment
  (confirmed to be the case for this project's actual deployment), that
  firewall rule is the only thing standing between live DICOM pixel data
  and anyone who can get onto the same network segment. See 5c.

### 5b. A real certificate for Kasm's gateway

Kasm's own official procedure
(<https://docs.kasm.com/docs/how-to/networking/replace-certificates>) is
short: it swaps two files and restarts one service. The commands below
assume a Let's Encrypt cert obtained via `certbot`; the same file-copy
step works with a cert from GUMed's own internal CA instead, if one exists.

```bash
sudo apt install certbot
# Kasm's own proxy already owns port 443/80, so certbot can't bind them
# while Kasm is running -- stop it for the (few-second) issuance/renewal
# window rather than fighting over the port. If GUMed's DNS provider has a
# certbot DNS-01 plugin, that avoids this downtime entirely (no port needed
# at all) -- worth it for the live deployment, just not spelled out here
# since it depends on which DNS provider GUMed actually uses.
sudo certbot certonly --standalone -d <your-kasm-hostname> \
  --pre-hook "systemctl stop kasm" --post-hook "systemctl start kasm"

sudo cp /etc/letsencrypt/live/<your-kasm-hostname>/fullchain.pem \
  /opt/kasm/current/certs/kasm_nginx.crt
sudo cp /etc/letsencrypt/live/<your-kasm-hostname>/privkey.pem \
  /opt/kasm/current/certs/kasm_nginx.key
sudo systemctl restart kasm
```

Let's Encrypt certs expire every 90 days — certbot installs its own renewal
timer, but it doesn't know about Kasm's non-standard cert location on its
own. Wire that up with a deploy-hook so renewal actually reaches Kasm,
instead of silently renewing a file Kasm never re-reads:

```bash
sudo tee /etc/letsencrypt/renewal-hooks/deploy/kasm-cert.sh <<'EOF'
#!/bin/bash
cp /etc/letsencrypt/live/<your-kasm-hostname>/fullchain.pem /opt/kasm/current/certs/kasm_nginx.crt
cp /etc/letsencrypt/live/<your-kasm-hostname>/privkey.pem /opt/kasm/current/certs/kasm_nginx.key
systemctl restart kasm
EOF
sudo chmod +x /etc/letsencrypt/renewal-hooks/deploy/kasm-cert.sh
```

Once this is in place, drop `--insecure` from every real `create-session.py`
invocation (it should only ever appear against a self-signed local/dev
Kasm instance, never here) and use the real `https://<your-kasm-hostname>`
as `KASM_SERVER`.

### 5c. Encrypting the Kasm-host ↔ Orthanc/viewer link (WireGuard)

Only needed for *this document's* separate-host setup, and only if that
link isn't already a fully private, physically-controlled segment — but
that's the actual case here, so treat this as required, not optional.

On **both** hosts:
```bash
sudo apt install wireguard
wg genkey | sudo tee /etc/wireguard/privatekey | wg pubkey | sudo tee /etc/wireguard/publickey
sudo chmod 600 /etc/wireguard/privatekey
```
Then `/etc/wireguard/wg0.conf`, substituting each host's own private key,
its own tunnel IP, and the *other* host's public key + real reachable
address:
```ini
# On the Kasm host (tunnel IP 10.10.0.1):
[Interface]
PrivateKey = <Kasm host's privatekey>
Address = 10.10.0.1/24
ListenPort = 51820

[Peer]
PublicKey = <Proxmox VM's publickey>
Endpoint = <Proxmox VM's real reachable IP>:51820
AllowedIPs = 10.10.0.2/32
PersistentKeepalive = 25
```
```ini
# On the Proxmox VM (tunnel IP 10.10.0.2):
[Interface]
PrivateKey = <Proxmox VM's privatekey>
Address = 10.10.0.2/24
ListenPort = 51820

[Peer]
PublicKey = <Kasm host's publickey>
Endpoint = <Kasm host's real reachable IP>:51820
AllowedIPs = 10.10.0.1/32
PersistentKeepalive = 25
```
```bash
sudo systemctl enable --now wg-quick@wg0
```

Then, on the Proxmox VM, bind this repo's own ports to the tunnel interface
instead of every interface — `docker-compose.remote-host.yml` already
parameterizes this via `BIND_ADDR` (see the warning comment at its top),
so this is a one-line `.env` change, not a compose edit:
```bash
# .env on the Proxmox VM
BIND_ADDR=10.10.0.2
```
Recreate the stack (`docker compose -f docker-compose.remote-host.yml up -d`)
and confirm with `ss -tlnp` that `8042`/`8043`/`8080` no longer listen on
the VM's real network interface at all — this is defense-in-depth beyond
the firewall rule in step 3: even a misconfigured firewall can't expose a
port the service isn't listening on in the first place. Update step 3's
firewall rule to allow from `10.10.0.1` (the Kasm host's tunnel IP) instead
of its real address, and point the Kasm workspace's Docker Run Config
Override (step 4) at `10.10.0.2` instead of the VM's real address.

### 5d. Before this actually goes public — known, tracked, not solved here

- Kasm Workspaces **Community Edition** is non-commercial-use-only and
  capped at 5 concurrent sessions (CLAUDE.md's own hard rule) — directly
  relevant once real radiologists connect from anywhere, not just this
  MVP's testing. Already tracked separately; this document doesn't change
  that.
- Putting Kasm's own public login/session endpoints directly on the open
  internet raises considerations this document doesn't cover (rate
  limiting in front of Kasm itself, DDoS exposure, whether a WAF/reverse
  proxy belongs in front of it) — worth its own issue before the live
  stage, not folded into this one.

## What's unchanged

Kasm Workspaces installation, the workspace image itself, DLP group
settings, `scripts/create-session.py`, the watermark — none of that cares
where Orthanc/viewer physically run. This document only covers the pieces
that actually differ.
