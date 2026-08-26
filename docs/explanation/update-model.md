# Update model

ChannelWatch v0.9.18 introduced the repaired hybrid update model. Starting with v1.0.0, release numbering makes the delivery method predictable:

- use the normal container update path for image and runtime changes;
- use the signed in-app Update Center by default for compatible app releases.

## Version and delivery cadence

From v1.0.0 forward, every minor line begins with one deliberate image milestone:

- `X.Y.0` requires the matching Docker, Unraid, Compose, or Helm image;
- `X.Y.1` through `X.Y.9` use the signed in-app Update Center;
- after `X.Y.9`, the next version is `X.(Y+1).0`, never `X.Y.10`.

For example, v1.0.0 establishes the container runtime for v1.0.1 through v1.0.9. The next planned image refresh is v1.1.0. Release tooling rejects a `.0` release that is not image-required, rejects a `.1` through `.9` release that is image-required, and rejects patch numbers above 9. A separately documented emergency may require a new minor `.0` milestone sooner, but an existing published version is never reclassified or replaced.

The default policy installs verified compatible updates during the local 03:00–05:00 maintenance window. Administrators can choose notify-only, apply immediately, postpone for 24 hours or 7 days, retry a failed attempt, or roll back. This keeps routine updates simple without pretending that a running container can safely replace its own base image, Python runtime, system packages, or launcher contract.

When legacy credential recovery prevents normal administrator navigation, a separate recovery surface can use only the compiled-in official signed stable channel. It is active only during that recovery state and requires normal administrator CSRF or a short-lived same-origin bootstrap CSRF plus exact typed confirmation. It does not accept alternate catalogs, URLs, uploads, signing keys, or downgrades.

## App bundle updates

An app bundle contains only ChannelWatch runtime code and static UI assets that are compatible with the current image runtime. Bundles are stored under `/config/channelwatch-runtime/releases/` and activated through `/config/channelwatch-runtime/active.json`.

Each bundle must match the current:

- runtime ABI;
- settings schema version;
- trusted release host policy;
- signature key;
- SHA256 digest.

The status surface reports the application version separately from the container image version, plus the runtime source and launcher protocol. An older compatible image can therefore run a newer verified app bundle without implying that the portal replaced the container image.

For the one-time v0.9.18 bridge, operational v0.9.11–v0.9.17 images have a compatible launcher and can update directly in the portal. This prominently includes v0.9.15, v0.9.16, and v0.9.17. The immutable published v0.9.9 and v0.9.10 entrypoints are exceptions: preserve `/config` and pull/recreate the v0.9.18 image once. After that image refresh, v0.9.18 manages future compatible in-app updates normally.

If those checks pass, the image-stable launcher can run the active bundle instead of the image copy of the app.

Every activation receives a unique generation identifier and a bounded validation deadline. The core and UI must each report a healthy startup for that exact generation before ChannelWatch marks the update successful. A stale readiness marker from an older process cannot validate a newer bundle. If either component fails or the deadline expires, the image-owned launcher restores the prior runtime selection and requests one coordinated container restart.

## Image-required updates

An update is image-required when it needs something the current container image cannot provide safely. Examples include dependency changes, base image updates, OS package changes, Supervisor changes, persistent schema changes, and deployment chart assumptions.

Beginning with v1.0.0, ChannelWatch intentionally groups those runtime refreshes into `X.Y.0` milestones. Even when a particular `.0` release contains mostly application changes, installing its matching image creates one clear and testable runtime baseline for the following nine in-app releases.

Image-required releases stay on the normal Docker, Unraid, Compose, or Helm update path. Deployment documentation or template presentation by itself is not image-required when the signed bundle remains compatible with the installed ABI, schema, dependencies, and image-owned launcher.

This boundary keeps the updater's authority narrow. The in-app path can select application code that the installed image is already capable of running, but it cannot change the interpreter or operating-system layer beneath that code. When a release crosses that boundary, ChannelWatch reports the required container update instead of attempting a partial installation.

The distinction also makes recovery predictable across supported deployment platforms. App-bundle recovery belongs to the image-owned launcher and its durable activation record, while image recovery remains the responsibility of Docker, Compose, Unraid, or Helm. Operators therefore retain the rollback mechanism provided by their deployment system without granting ChannelWatch access to the host Docker socket or cluster credentials.

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
