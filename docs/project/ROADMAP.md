# Roadmap

This roadmap is intentionally conservative. It only lists work that is still open after the v0.9 documentation and feature pass, or work that follows directly from the current architecture.

Items here are goals, not promises.

## v1.1 focus

### Multi-DVR polish

- tighten the remaining rough edges in aggregate versus per-DVR behavior
- improve the operator story around DVR rename, replacement, and long-term identity changes
- expand the docs and tests around DVR archive and restore workflows

### Deployment hardening

- publish clearer Helm usage docs around the existing single-replica chart
- improve environment-limited chart verification in CI or release tooling
- keep container health and probe behavior aligned across Docker and Kubernetes examples

### Release and supply chain follow-up

- extend release hardening beyond provenance attestation
- add image signing and SBOM publishing when the container publishing path supports them
- keep dependency update coverage current for both Python and UI dependencies

### Operator experience

- continue tightening error messages, diagnostics, and doctor CLI guidance
- keep backup, restore, and debug-bundle flows easy to reason about
- improve upgrade notes when schema or operational behavior changes

## v1.x longer-term work

### Auth and access controls

- continue hardening the RBAC setup path
- reduce reliance on the shared API key in mixed-mode deployments
- improve documentation for reverse-proxy and TLS-first deployments

### Notifications and integrations

- keep expanding provider docs, templates, and routing examples
- improve plugin author ergonomics without widening the trust boundary
- keep delivery logs, retries, and webhook tooling operator-friendly

### Maintainer resilience

- reduce single-maintainer risk over time
- improve maintainer handoff notes and backup admin coverage when practical

## v2.0 themes

v2.0 is where larger architectural changes belong. The current app is a strong single-instance self-hosted service. Bigger shifts should wait for a major release.

Possible v2.0 themes:

- a cleaner public API contract for external integrations
- a more explicit long-term migration story for major storage or schema changes
- broader deployment options if the app ever moves beyond the current single-writer `/config` model
- a deeper security posture refresh once the authentication and release pipeline work has matured

## Not on this roadmap yet

These are intentionally not claimed as finished or scheduled here:

- multi-replica support
- public internet exposure without a proper reverse proxy and TLS
- automated cloud services run by ChannelWatch itself
- telemetry, analytics, or managed-hosted features

ChannelWatch remains a self-hosted, operator-controlled project first.
