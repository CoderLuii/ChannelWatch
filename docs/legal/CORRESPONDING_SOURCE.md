# ChannelWatch v0.9.18 Corresponding Source and Rebuild Map

<!-- cspell:ignore libgcc libstdc zstd -->

This document maps the copyleft-licensed packages identified in the exact
ChannelWatch v0.9.18 container SBOMs to their source and build recipes. The
published amd64 and arm64 SBOMs remain the authoritative package inventory.

ChannelWatch does not modify these upstream packages. The final image is built
from the public ChannelWatch repository and pinned Chainguard/Wolfi inputs in
`deploy/docker/Dockerfile`.

## Exact container inputs

- Runtime image index: `cgr.dev/chainguard/python:latest@sha256:1f6779775c9f466890da563e411cb677045a6c20b6a65160eefad1deffb5012c`
- Build image index: `cgr.dev/chainguard/python:latest-dev@sha256:4bf7e945777010672b8ccd5d2ae2c41c91ad6d3478878347c731ae536d506bef`
- Reviewed Wolfi recipe tree: [`wolfi-dev/os@8190a1652f4534ad3feebd3b48066514f0f4375f`](https://github.com/wolfi-dev/os/tree/8190a1652f4534ad3feebd3b48066514f0f4375f)

## Package-to-source mapping

| Image package | Declared license | Exact source and packaging recipe |
|---|---|---|
| `gdbm 1.26-r5` | GPL-3.0-or-later | [Wolfi recipe](https://github.com/wolfi-dev/os/blob/8190a1652f4534ad3feebd3b48066514f0f4375f/gdbm.yaml); upstream [`gdbm-1.26.tar.gz`](https://ftp.gnu.org/gnu/gdbm/gdbm-1.26.tar.gz), SHA-256 `6a24504a14de4a744103dcb936be976df6fbe88ccff26065e54c1c47946f4a5e` |
| `glibc-2.43 2.43-r15`, `glibc-2.43-locale-posix 2.43-r15`, `ld-linux-2.43 2.43-r15` | LGPL-2.1-or-later | [Wolfi recipe and patches](https://github.com/wolfi-dev/os/blob/8190a1652f4534ad3feebd3b48066514f0f4375f/glibc-2.43.yaml); upstream [`glibc` commit `dae425b554207f7c4599c7fac707ad4c08545674`](https://gitlab.com/gnutools/glibc/-/commit/dae425b554207f7c4599c7fac707ad4c08545674) |
| `libgcc 16.2.0-r0`, `libstdc++ 16.2.0-r0` | GPL-3.0-or-later WITH GCC-exception-3.1 | [Wolfi recipe and patch](https://github.com/wolfi-dev/os/blob/8190a1652f4534ad3feebd3b48066514f0f4375f/gcc.yaml); upstream [`gcc` commit `78d4ac73dd391005b895a6148cd9831e28e1208b`](https://gitlab.com/gnutools/gcc/-/commit/78d4ac73dd391005b895a6148cd9831e28e1208b) |
| `libuuid 2.42.2-r3` | Mixed GPL/LGPL/BSD/MIT/CC-PDDC metadata | [Wolfi recipe](https://github.com/wolfi-dev/os/blob/8190a1652f4534ad3feebd3b48066514f0f4375f/util-linux.yaml); [upstream `util-linux-2.42.2.tar.xz`](https://www.kernel.org/pub/linux/utils/util-linux/v2.42/util-linux-2.42.2.tar.xz), SHA-256 `03a05d3adf9602ef128f2da05b84b3205ce60c351e5737c0370f74000679ce8a` |
| `libzstd1 1.5.7-r8` | BSD-2-Clause AND GPL-2.0-only | [Wolfi recipe](https://github.com/wolfi-dev/os/blob/8190a1652f4534ad3feebd3b48066514f0f4375f/zstd.yaml); upstream [`zstd` commit `f8745da6ff1ad1e7bab384bd1f9d742439278e99`](https://github.com/facebook/zstd/commit/f8745da6ff1ad1e7bab384bd1f9d742439278e99) |
| `readline 8.3-r2` | GPL-3.0-or-later | [Wolfi recipe](https://github.com/wolfi-dev/os/blob/8190a1652f4534ad3feebd3b48066514f0f4375f/readline.yaml); upstream [`readline-8.3.tar.gz`](https://ftp.gnu.org/gnu/readline/readline-8.3.tar.gz), SHA-256 `fe5383204467828cd495ee8d1d3c037a7eba1389c22bc6a041f627976f9061cc` |
| `xz 5.8.3-r2` | GPL-3.0-or-later | [Wolfi recipe](https://github.com/wolfi-dev/os/blob/8190a1652f4534ad3feebd3b48066514f0f4375f/xz.yaml); upstream [`xz` commit `4b73f2ec19a99ef465282fbce633e8deb33691b3`](https://github.com/tukaani-project/xz/commit/4b73f2ec19a99ef465282fbce633e8deb33691b3) |
| `zeroconf 0.150.0` | LGPL-2.1-or-later | [PyPI source archive](https://files.pythonhosted.org/packages/09/ea/34bb185645ecaa18d34e5883bffea71aa9bffbbb994634884e8b2f3ad0c4/zeroconf-0.150.0.tar.gz), SHA-256 `a5fe7feab1de6ef5e541e0a3d07e534fd91629b813fc27281593584100f63164`; [project source](https://github.com/python-zeroconf/python-zeroconf/tree/0.150.0) |

The Wolfi recipes above contain the exact package version, revision (`epoch`),
upstream commit or archive digest, patches, configuration, build steps, and
subpackage split used for the package identifiers in the SBOM. The accompanying
copyleft-license archive contains the complete GPL 1.0, GPL 2.0, GPL 3.0,
LGPL 2.1, and GCC Runtime Library Exception 3.1 texts from the pinned
[`spdx/license-list-data`](https://github.com/spdx/license-list-data/tree/5bf6d9610255540bfbee6890765a616042bf1e11)
revision.

## Rebuild and replacement

Checkout the exact ChannelWatch release tag and run:

```sh
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  --file deploy/docker/Dockerfile \
  --build-arg VERSION=0.9.18 \
  --build-arg GIT_SHA="$(git rev-parse HEAD)" \
  --output type=oci,dest=channelwatch-v0.9.18.oci \
  .
```

To replace `zeroconf` with a modified compatible build, change its pinned entry
in `deploy/requirements/runtime.constraints.txt`, make the corresponding source
available to `pip`, and rebuild the image with the same Dockerfile. ChannelWatch
imports `zeroconf` dynamically from the Python environment and does not prevent
replacement with a modified compatible version.

For source-availability questions about the v0.9.18 distribution, open a GitHub
Discussion or issue in the ChannelWatch repository and identify the release tag,
image registry, architecture, package name, and SBOM package version.
