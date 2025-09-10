# Keybr.com (Home Assistant add-on)

Builds the upstream keybr.com SPA and serves it through Nginx with Ingress.

- Upstream: https://github.com/aradzie/keybr.com (AGPL-3.0)
- Exposes: Ingress (no direct host ports by default)
- Architecture: amd64, aarch64, armv7

## Build & install

1. Place this folder inside your local add-ons repo.
2. In **Home Assistant → Settings → Add-ons → Add-on Store → ⋮ → Repositories**, add your repo if needed.
3. Open the add-on, click **Build**, then **Install**, then **Start**.
4. Click **Open Web UI** (served via Ingress).

## Notes

- This image builds the site at image build time (faster start, no dev toolchain at runtime).
- To pin a different branch or fork, rebuild with build args:
  - `REPO_URL` (default: official repo)
  - `REPO_REF` (default: `master`)
