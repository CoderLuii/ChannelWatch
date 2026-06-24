# Update model

ChannelWatch v0.9.9 introduces a hybrid update model:

- use the normal container update path for image and runtime changes;
- use the in-app Update Center for compatible app-only releases.

This is deliberate. It gives users a simple web UI for routine app updates without pretending that a running container can safely replace its own base image, Python runtime, system packages, or deployment contract.

## App bundle updates

An app bundle contains only ChannelWatch runtime code and static UI assets that are compatible with the current image runtime. Bundles are stored under `/config/channelwatch-runtime/releases/` and activated through `/config/channelwatch-runtime/active.json`.

Each bundle must match the current:

- runtime ABI;
- settings schema version;
- trusted release host policy;
- signature key;
- SHA256 digest.

If those checks pass, the image-stable launcher can run the active bundle instead of the image copy of the app.

## Image-required updates

An update is image-required when it needs something the current container image cannot provide safely. Examples include dependency changes, base image updates, OS package changes, Supervisor changes, persistent schema changes, and deployment chart assumptions.

Image-required releases stay on the normal Docker, Unraid, Compose, or Helm update path.

## Startup precedence

At container startup, the image entrypoint resolves the active runtime:

1. A newer compatible active bundle wins over the image copy.
2. A same-version or older active bundle is ignored in favor of the image.
3. Runtime ABI or schema mismatch falls back to the image.
4. Missing or corrupt active metadata falls back to the image.

Supervisor always starts an image-owned runtime launcher first. The launcher then imports the selected app directory. If the selected bundle fails during activation, the launcher records the failure and rolls back to the previous runtime or the image copy.

## Why not replace the Docker image in-app?

Replacing the container image from inside the app would require control over the host Docker socket, Unraid template state, Compose files, Helm releases, permissions, and restart policy. That would increase risk and vary by platform.

The safer user experience is explicit:

- compatible app updates happen in-app;
- runtime-changing updates say **container image update required**.

That keeps the simple path simple and the dangerous path visible.
