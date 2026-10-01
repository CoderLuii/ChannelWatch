# ChannelWatch Corresponding Source and Rebuild Map

<!-- cspell:ignore libgcc libstdc zstd -->

This document maps the copyleft-licensed packages identified in the exact
ChannelWatch container SBOMs to their source and build recipes. The
published amd64 and arm64 SBOMs remain the authoritative package inventory.

ChannelWatch does not modify these upstream packages. The final image is built
from the public ChannelWatch repository and pinned Chainguard/Wolfi inputs in
`deploy/docker/Dockerfile`.

## Exact container inputs

- Runtime image index: `cgr.dev/chainguard/python:latest@sha256:38ba1cbf71702bacc5f5be22ea41e3d4ad1bfb2565413b0caf4bafde38e831f2`
- Build image index: `cgr.dev/chainguard/python:latest-dev@sha256:83933e374c3c3250e5b1b5dcde789a2d4a7314b771618b5548adab01c54066a0`
- Reviewed Wolfi recipe tree: [`wolfi-dev/os@9ba86882d6884d680d27a9ac3b3c5a66a83e1c25`](https://github.com/wolfi-dev/os/tree/9ba86882d6884d680d27a9ac3b3c5a66a83e1c25)

## Package-to-source mapping

| Image package | Declared license | Exact source and packaging recipe |
|---|---|---|
| `gdbm 1.26-r6` | GPL-3.0-or-later | [Wolfi recipe](https://github.com/wolfi-dev/os/blob/9ba86882d6884d680d27a9ac3b3c5a66a83e1c25/gdbm.yaml); upstream [`gdbm` commit `8ca8f03ab6cc125139a395ec2d522aebb15f8060`](https://git.savannah.gnu.org/cgit/gdbm.git/commit/?id=8ca8f03ab6cc125139a395ec2d522aebb15f8060) |
| `glibc-2.44 2.44-r7`, `glibc-2.44-locale-posix 2.44-r7`, `ld-linux-2.44 2.44-r7` | LGPL-2.1-or-later | [Wolfi recipe and patches](https://github.com/wolfi-dev/os/blob/9ba86882d6884d680d27a9ac3b3c5a66a83e1c25/glibc-2.44.yaml); upstream [`glibc` commit `b4f51887c48ac82acfdb13f96d9eab5d120cb2b7`](https://sourceware.org/git/?p=glibc.git;a=commit;h=b4f51887c48ac82acfdb13f96d9eab5d120cb2b7) |
| `libgcc 16.2.0-r1`, `libstdc++ 16.2.0-r1` | GPL-3.0-or-later WITH GCC-exception-3.1 | [Wolfi recipe and patch](https://github.com/wolfi-dev/os/blob/9ba86882d6884d680d27a9ac3b3c5a66a83e1c25/gcc.yaml); upstream [`gcc` commit `78d4ac73dd391005b895a6148cd9831e28e1208b`](https://gitlab.com/gnutools/gcc/-/commit/78d4ac73dd391005b895a6148cd9831e28e1208b) |
| `libuuid 2.42.4-r0` | Mixed GPL/LGPL/BSD/MIT/CC-PDDC metadata | [Wolfi recipe](https://github.com/wolfi-dev/os/blob/9ba86882d6884d680d27a9ac3b3c5a66a83e1c25/util-linux.yaml); upstream [`util-linux` commit `d76cbf8f13e65ff657344f7f6a90042cf755ba59`](https://github.com/util-linux/util-linux/commit/d76cbf8f13e65ff657344f7f6a90042cf755ba59) |
| `libzstd1 1.5.7-r10` | BSD-2-Clause AND GPL-2.0-only | [Wolfi recipe](https://github.com/wolfi-dev/os/blob/9ba86882d6884d680d27a9ac3b3c5a66a83e1c25/zstd.yaml); upstream [`zstd` commit `f8745da6ff1ad1e7bab384bd1f9d742439278e99`](https://github.com/facebook/zstd/commit/f8745da6ff1ad1e7bab384bd1f9d742439278e99) |
| `readline 8.3-r3` | GPL-3.0-or-later | [Wolfi recipe](https://github.com/wolfi-dev/os/blob/9ba86882d6884d680d27a9ac3b3c5a66a83e1c25/readline.yaml); upstream [`readline` commit `447b8290b3e2e2d117dc8e9cdb83b0dc6448a638`](https://git.savannah.gnu.org/cgit/readline.git/commit/?id=447b8290b3e2e2d117dc8e9cdb83b0dc6448a638) |
| `xz 5.8.4-r0` | GPL-3.0-or-later | [Wolfi recipe](https://github.com/wolfi-dev/os/blob/9ba86882d6884d680d27a9ac3b3c5a66a83e1c25/xz.yaml); upstream [`xz` commit `d3e650e63c110e830fd5391e7f8b45df0b91d3da`](https://github.com/tukaani-project/xz/commit/d3e650e63c110e830fd5391e7f8b45df0b91d3da) |
| `zeroconf 0.151.5` | LGPL-2.1-or-later | [PyPI source archive](https://files.pythonhosted.org/packages/93/20/69744d9de9d375dae2639dda9add7dae85d52d690b68c61b23b8a6468434/zeroconf-0.151.5.tar.gz), SHA-256 `28c2ec9d772007eedf11b41a9c9fd3d5c684c17b00721ff8f1ee31b20ad286a1`; [project source](https://github.com/python-zeroconf/python-zeroconf/tree/0.151.5) |

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
version="$(python3 -c 'import json; print(json.load(open("scripts/release/release-config.json"))["version"])')"
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  --file deploy/docker/Dockerfile \
  --build-arg VERSION="${version}" \
  --build-arg GIT_SHA="$(git rev-parse HEAD)" \
  --output "type=oci,dest=channelwatch-v${version}.oci" \
  .
```

To replace `zeroconf` with a modified compatible build, change its pinned entry
in `deploy/requirements/runtime.constraints.txt`, make the corresponding source
available to `pip`, and rebuild the image with the same Dockerfile. ChannelWatch
imports `zeroconf` dynamically from the Python environment and does not prevent
replacement with a modified compatible version.

For source-availability questions about this distribution, open a GitHub
Discussion or issue in the ChannelWatch repository and identify the release tag,
image registry, architecture, package name, and SBOM package version.
