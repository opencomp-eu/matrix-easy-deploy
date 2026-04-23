# Automated releasing

This repository includes a release pipeline in `.github/workflows/release.yml`.

## Trigger

Any push to the `release` branch triggers the full pipeline.

The workflow will:

1. Read `VERSION` (must be `x.y.z`)
2. Create a unique tag: `v<VERSION>-r<GITHUB_RUN_NUMBER>`
3. Publish a GitHub Release with a source tarball + sha256 file
4. Build and push a Docker image to GHCR
5. Build and push a Docker image to Docker Hub (if Docker Hub secrets are set)
6. Update a Homebrew tap formula (if Homebrew tap secrets are set)

## Required secrets

### For GitHub Release + GHCR

No extra secrets are required beyond the default `GITHUB_TOKEN`.

### For Docker Hub publishing (optional)

- `DOCKERHUB_USERNAME`
- `DOCKERHUB_TOKEN` (Docker Hub access token)

### For Homebrew tap update (optional)

- `HOMEBREW_TAP_REPO` (example: `your-org/homebrew-tap`)
- `HOMEBREW_TAP_TOKEN` (PAT with repo write access to the tap repository)

## Image names

GHCR:

- `ghcr.io/<owner>/<repo>:v<VERSION>-r<RUN_NUMBER>`
- `ghcr.io/<owner>/<repo>:release-latest`

Docker Hub:

- `docker.io/<DOCKERHUB_USERNAME>/matrix-easy-deploy:v<VERSION>-r<RUN_NUMBER>`
- `docker.io/<DOCKERHUB_USERNAME>/matrix-easy-deploy:release-latest`

## Homebrew formula output

If Homebrew secrets are set, the workflow writes/updates:

- `Formula/med-kit.rb` in your tap repository

Installed commands:

- `med-kit`
- `med-kit-start`
- `med-kit-stop`
- `med-kit-update`

## Notes

- The pipeline does **not** perform telemetry or active deployment checks.
- Each run creates a unique release tag, so every commit to `release` produces a distinct release.
- To publish a new logical version, update `VERSION` in your commit.
