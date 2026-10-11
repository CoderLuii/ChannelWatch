# Update model

ChannelWatch v0.9.18 introduced the repaired hybrid update model. Starting with v1.0.0, release numbering makes the delivery method predictable:

- use the normal container update path for image and runtime changes;
- use the signed in-app Update Center by default for compatible app releases.

## Version and delivery cadence

Published v1.0.0 through v1.4.x lines follow one image milestone per minor line:

- `X.Y.0` requires the matching Docker, Unraid, Compose, or Helm image;
- `X.Y.1` through `X.Y.9` use the signed in-app Update Center;
- after `X.Y.9`, the next version is `X.(Y+1).0`, never `X.Y.10`.

For example, v1.2.0 establishes the container runtime for v1.2.1 through v1.2.9. Published versions keep their original delivery requirements. Patch numbers still stop at 9.

After the verified protocol-4 image milestone at v1.5.3, v1.5.4 retains that image floor and delivers a complete signed runtime generation. A later minor line may retain a proven image floor only with the exact immutable bootstrap identity plus compatibility, restart, and rollback evidence on both native AMD64 and native ARM64. Without that evidence, the new `.0` release requires its matching image. Changes to the interpreter, OS, Supervisor, or image-owned verification code still require an image milestone.

An image milestone without a published GitHub release can be recovered by a later patch in the same minor line. The release tooling verifies that every intervening tag has no GitHub release, keeps those versions out of signed update catalog history, and requires the replacement's matching image. v1.5.3 follows this path after v1.5.0 failed in source testing, v1.5.1 failed during candidate signing because its publication time preceded the candidate commit, and v1.5.2 failed during native migration qualification because the release verifier environment lacked `httpx`. None produced a GitHub release, release assets, or container image; all three original tags remain unchanged. Future runtime generations must name the actual published image and its exact digest.

The default policy installs verified compatible updates during the local 03:00–05:00 maintenance window. Administrators can choose notify-only, apply immediately, postpone for 24 hours or 7 days, retry a failed attempt, or roll back. This keeps routine updates simple without pretending that a running container can safely replace its own base image, Python runtime, system packages, or launcher contract.

The signed catalog can contain both a newer image milestone and an older app release that the installed image can still run. In that case, Update Center offers the highest compatible forward app release and shows the newer container image as a separate recommendation. It never downgrades the application, skips signature checks, or treats an incompatible bundle as installable. After the app update, the newer image milestone remains visible until the container is refreshed.

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

The reconnect code was written for v1.4.2. Publications of v1.4.2 through v1.4.4 failed before any release assets were published, so it first ships in v1.4.5. The browser follows the durable update job across temporary connection failures and page reloads. It waits for the matching job to succeed, the requested application version to become active, and two consecutive runtime and Settings checks before refreshing the dashboard. A terminal failure remains visible. The browser's three-minute reconnect budget gives the launcher's two-minute validation deadline time to finish; it does not extend that deadline or submit a second update.

For manual Apply and Retry actions, the browser assigns the job ID before sending the request. The authenticated updater retains that ID in its existing activation record, so a lost reply does not force the browser to guess which update completed. Signature verification and the server's single-flight lock still govern installation.

## Dependency runtime generations

Images with launcher protocol 4 can verify an application and its Python
packages as one signed runtime generation. Each generation contains the core,
backend, static frontend, dependency files, package notices and platform
inventory. Preparation happens in an inactive directory under `/config`; it
does not install packages into the running image's `/venv` or operating system.

The image-owned launcher checks the signed release record, descriptor,
dependency inventory and prepared environment before importing selected code.
Core and UI start in fresh processes using the same generation. Both must
report that exact activation as healthy before installation succeeds. Startup
failure restores the previous application and dependency selection together.

Published v1.4.x images do not have this verification authority.
They require one container image update before dependency generations can be
used. An application-only download cannot replace their immutable launchers.
Preserve `/config` and any external storage key during that image update.

v1.5.0, v1.5.1, and v1.5.2 failed before release assets or images were published.
v1.5.3 recovers that milestone and is the first published image with this authority.
It supplies the image foundation and does not publish a runtime-generation
payload. v1.5.4 is the first compatible release to use that foundation: it
updates the application and its complete 40-package official Python closure
together without recreating the container.

The older-client restart report in [issue #22](https://github.com/CoderLuii/ChannelWatch/issues/22)
remains open. Its cause has not been proven, and this image foundation does not
present it as resolved.

This capability covers compatible Python packages, including qualified binary
wheels. v1.5.4 updates eight packages while signing and validating the complete
40-package closure. It does not replace Python itself, the operating system, glibc,
Supervisor or the image-owned verifier. Those components still need image
maintenance. Updating a worker package does not repair a vulnerability in the
older image copy of that package.

Dependency-aware clients use the signed v3 catalog. Existing clients keep
their v2 app-only catalog and published release history. Every protocol-4
application update needs a complete signed generation, even when its package
versions have not changed; a missing identity cannot downgrade startup checks.

The dependency catalog can retain the previous complete runtime release as a
compatible fallback within its size limit. Older release history remains
visible, but a record without its signed package variants is not offered as an
installable dependency update. This does not remove the last good generation
already saved in `/config` for local rollback.

Preparation requires writable, executable persistent storage and enough space
for the download, expanded candidate, previous generation and preparation
headroom. Unsupported mounts or insufficient space stop installation before
selection changes. Keep the active and last good generations; cleanup removes
only unreferenced, authenticated generations after successful activation.

## Remaining image-required updates

An update is image-required when it needs something the current container image cannot provide safely. Examples include incompatible dependencies, base image updates, OS package changes, Supervisor changes, persistent schema changes, and deployment chart assumptions. Images without protocol-4 dependency support also need an image update for package changes.

ChannelWatch groups image-required runtime changes into `X.Y.0` milestones. A future minor line may keep a proven protocol-4 image baseline only when release tooling verifies the complete dependency-generation evidence described above.

Image-required releases stay on the normal Docker, Unraid, Compose, or Helm update path. Deployment documentation or template presentation by itself is not image-required when the signed bundle remains compatible with the installed ABI, schema, dependencies, and image-owned launcher.

This boundary keeps the updater's authority narrow. The in-app path can select application code that the installed image is already capable of running, but it cannot change the interpreter or operating-system layer beneath that code. When a release crosses that boundary, ChannelWatch reports the required container update instead of attempting a partial installation.

The distinction also makes recovery predictable across supported deployment platforms. App-bundle recovery belongs to the image-owned launcher and its durable activation record, while image recovery remains the responsibility of Docker, Compose, Unraid, or Helm. Operators therefore retain the rollback mechanism provided by their deployment system without granting ChannelWatch access to the host Docker socket or cluster credentials.

## Startup precedence

At container startup, the image entrypoint resolves the active runtime:

1. A newer compatible active bundle wins over the image copy.
2. A same-version or older active bundle is ignored in favor of the image.
3. Runtime ABI or schema mismatch falls back to the image.
4. Missing or corrupt active metadata falls back to the image.

Supervisor always starts an image-owned runtime launcher first. Protocol 4 authenticates every selected generation before importing its app or packages; missing dependency identity cannot select an unverified app. If the selected bundle fails during activation, the launcher records the failure and rolls back to the previous runtime or the image copy.

## Why not replace the Docker image in-app?

Replacing the container image from inside the app would require control over the host Docker socket, Unraid template state, Compose files, Helm releases, permissions, and restart policy. That would increase risk and vary by platform.

Update Center distinguishes the two delivery paths:

- compatible app and supported package updates happen in-app;
- interpreter, OS, or image-owned launcher changes say **container image update required**.
