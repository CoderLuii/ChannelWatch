# ChannelWatch Corresponding Source and Rebuild Map

This document records the source and notice material for the Debian components in the ChannelWatch v1.3.0 container image. ChannelWatch does not modify these upstream components. The final release SBOMs for the published amd64 and arm64 images are the authoritative package inventories.

<!-- cspell:ignore adduser apt passwd dpkg debconf debianutils diffutils e2fsprogs logsave gnupg gpgv acl attr libcap cdebconf libffi libxcrypt libgcrypt gmp gnutls libhogweed libnettle libidn libmd p11 libpam libtasn libunistring mawk netbase sysvinit usrmerge xxhash Sleepycat coreutils libbz -->

<!-- cspell:ignore bsdutils dfsg findutils libacl libapt libattr libaudit libblkid libc libcom libcrypt libdb libdebconfclient libext libgcc libgdbm libgmp libgnutls libgpg liblz libmount libncursesw libp libpcre libreadline libseccomp libsemanage libsepol libsmartcols libsqlite libss libstdc libsystemd libtinfo libudev libxxhash pcre -->

## Exact base inputs

- Image index: `docker.io/library/python@sha256:c8137f4c460908c8763f281c8f22c431eb5c538514ba9553fc3a89c06b7cfb88`
- amd64 manifest: `sha256:d1e795fbdab8a4744432467f32f348c6baa99f07abc05ffde710913f65c8261d`
- arm64 manifest: `sha256:6e1f3bd1526e54623c48ea9f79f91fe354f8c5a0430473db89528c9846951585`
- Base: Debian Bookworm Slim. The pinned manifests each contain the same 97 Debian package names and versions; only the architecture differs.
- Official Python build recipe: [`docker-library/python@7cc547b3ff8d45d540cd23144227af126a79d60c:3.14/slim-bookworm/Dockerfile`](https://github.com/docker-library/python/blob/7cc547b3ff8d45d540cd23144227af126a79d60c/3.14/slim-bookworm/Dockerfile)
- Python source: [`Python-3.14.8.tar.xz`](https://www.python.org/ftp/python/3.14.8/Python-3.14.8.tar.xz), SHA-256 `c2215904f02b175596dc49351585104f4bc20341e1c47378b26a2c274360ce73`.

The official recipe verifies that Python checksum, builds with the Debian toolchain, and starts from `debian:bookworm-slim`. Keep the image index pinned; do not substitute a mutable Python tag.

## Debian notice and source policy

The full notice set is not limited to `/usr/share/common-licenses`. Each installed Debian package also provides its applicable notices and license text in `/usr/share/doc/<package>/copyright`. The container retains those package copyright files for every installed Debian package. The release copyleft-license archive also includes these notices and the base common-license texts. This is required for mixed-license source families where a license may apply to only a component, example, or generated file.

The inspected image includes common-license texts for `GFDL-1.2`, `GFDL-1.3`, `GPL-1`, `GPL-2`, `GPL-3`, `LGPL-2`, `LGPL-2.1`, `LGPL-3`, `MPL-1.1`, and `MPL-2.0`, as well as Apache, Artistic, BSD, and CC0 texts. It does **not** provide every bespoke license as a standalone common-license file. In particular, the `libdb5.3` copyright file contains the Sleepycat notice. Do not replace package-specific copyright files with an incomplete selected list of SPDX texts.

## Copyleft and reciprocal-license source map

Versions come from `/var/lib/dpkg/status` in the pinned amd64 root filesystem. Where the status record names a source package and source version, that source version is used; a binary rebuild suffix such as `+b13` is not a source version. Links point to Debian's official source and packaging archive.

| Source family | Installed binary packages and source version | Notice/rebuild material |
|---|---|---|
| [adduser](https://sources.debian.org/src/adduser/3.134/) | `adduser` `3.134` | GPL-family notice in package copyright |
| [apt](https://sources.debian.org/src/apt/2.6.1/) | `apt`, `libapt-pkg6.0` `2.6.1` | GPL-family notice in package copyright |
| [base-files](https://sources.debian.org/src/base-files/12.4+deb12u15/) | `base-files` `12.4+deb12u15` | GPL-family notice in package copyright |
| [base-passwd](https://sources.debian.org/src/base-passwd/3.6.1/) | `base-passwd` `3.6.1` | GPL-family notice in package copyright |
| [bash](https://sources.debian.org/src/bash/5.2.15-2/) | `bash` binary `5.2.15-2+b13`; source `5.2.15-2` | GPL/GFDL notices in package copyright |
| [bzip2](https://sources.debian.org/src/bzip2/1.0.8-5/) | `libbz2-1.0` binary `1.0.8-5+b1`; source `1.0.8-5` | BSD variant and GPL-2.0 packaging notices in package copyright |
| [ca-certificates](https://snapshot.debian.org/package/ca-certificates/20250419~deb12u1/) | `ca-certificates` `20250419~deb12u1` | GPL and MPL-2.0 notices in package copyright |
| [coreutils](https://sources.debian.org/src/coreutils/9.1-1/) | `coreutils` `9.1-1` | GPL/GFDL notices in package copyright |
| [dash](https://sources.debian.org/src/dash/0.5.12-2/) | `dash` `0.5.12-2` | GPL-2.0-family notice in package copyright |
| [debconf](https://sources.debian.org/src/debconf/1.5.82/) | `debconf` `1.5.82` | GPL-family notice in package copyright |
| [debian-archive-keyring](https://sources.debian.org/src/debian-archive-keyring/2023.3+deb12u2/) | `debian-archive-keyring` `2023.3+deb12u2` | GPL-family notice in package copyright |
| [debianutils](https://sources.debian.org/src/debianutils/5.7-0.5~deb12u1/) | `debianutils` `5.7-0.5~deb12u1` | GPL-family notice in package copyright |
| [diffutils](https://sources.debian.org/src/diffutils/1%3A3.8-4/) | `diffutils` `1:3.8-4` | GPL, LGPL, and GFDL notices in package copyright |
| [dpkg](https://sources.debian.org/src/dpkg/1.21.23/) | `dpkg` `1.21.23` | GPL-family notice in package copyright |
| [e2fsprogs](https://sources.debian.org/src/e2fsprogs/1.47.0-2/) | `e2fsprogs`, `libcom-err2`, `libext2fs2`, `libss2`, `logsave`; binary `1.47.0-2+b2`, source `1.47.0-2` | GPL/LGPL notices in package copyright |
| [findutils](https://sources.debian.org/src/findutils/4.9.0-4/) | `findutils` `4.9.0-4` | GPL, LGPL, and GFDL notices in package copyright |
| [gcc-12](https://sources.debian.org/src/gcc-12/12.2.0-14+deb12u1/) | `gcc-12-base`, `libgcc-s1`, `libstdc++6` `12.2.0-14+deb12u1` | GPL-3.0-family notices and GCC Runtime Library Exception, as applicable |
| [gnupg2](https://sources.debian.org/src/gnupg2/2.2.40-1.1+deb12u2/) | `gpgv` `2.2.40-1.1+deb12u2` | GPL/LGPL notices in package copyright |
| [grep](https://sources.debian.org/src/grep/3.8-5/) | `grep` `3.8-5` | GPL-3.0-family notice in package copyright |
| [gzip](https://sources.debian.org/src/gzip/1.12-1/) | `gzip` `1.12-1` | GPL/GFDL notices in package copyright |
| [init-system-helpers](https://sources.debian.org/src/init-system-helpers/1.65.2+deb12u1/) | `init-system-helpers` `1.65.2+deb12u1` | GPL-family notice in package copyright |
| [hostname](https://sources.debian.org/src/hostname/3.23+nmu1/) | `hostname` `3.23+nmu1` | GPL-family notice in package copyright |
| [acl](https://sources.debian.org/src/acl/2.3.1-3/) | `libacl1` `2.3.1-3` | GPL/LGPL notices in package copyright |
| [attr](https://sources.debian.org/src/attr/1%3A2.5.1-4/) | `libattr1` `1:2.5.1-4` | GPL/LGPL notices in package copyright |
| [audit](https://sources.debian.org/src/audit/1%3A3.0.9-1/) | `libaudit-common`, `libaudit1` `1:3.0.9-1` | GPL/LGPL notices in package copyright |
| [util-linux](https://sources.debian.org/src/util-linux/2.38.1-5+deb12u3/) | `bsdutils`, `libblkid1`, `libmount1`, `libsmartcols1`, `libuuid1`, `mount`, `util-linux`, `util-linux-extra` `2.38.1-5+deb12u3` | Mixed GPL/LGPL/BSD notices in package copyright |
| [libcap-ng](https://sources.debian.org/src/libcap-ng/0.8.3-1/) | `libcap-ng0` binary `0.8.3-1+b3`; source `0.8.3-1` | GPL/LGPL notices in package copyright |
| [libcap2](https://sources.debian.org/src/libcap2/1%3A2.66-4+deb12u3/) | `libcap2` binary `1:2.66-4+deb12u3+b1`; source `1:2.66-4+deb12u3` | Package copyright notice |
| [db5.3](https://sources.debian.org/src/db5.3/5.3.28+dfsg2-1/) | `libdb5.3` `5.3.28+dfsg2-1` | Sleepycat, GPL, and other notices in package copyright |
| [cdebconf](https://sources.debian.org/src/cdebconf/0.270/) | `libdebconfclient0` `0.270` | GPL-family notice in package copyright |
| [libffi](https://sources.debian.org/src/libffi/3.4.4-1/) | `libffi8` `3.4.4-1` | MPL-1.1 OR GPL-2.0 OR LGPL-2.1 notice in package copyright |
| [glibc](https://sources.debian.org/src/glibc/2.36-9+deb12u14/) | `libc-bin`, `libc6` `2.36-9+deb12u14` | LGPL-2.1-family notice in package copyright |
| [libxcrypt](https://sources.debian.org/src/libxcrypt/1%3A4.4.33-2/) | `libcrypt1` `1:4.4.33-2`; source `1:4.4.33-2` | GPL/LGPL notices in package copyright |
| [libgcrypt20](https://sources.debian.org/src/libgcrypt20/1.10.1-3+deb12u1/) | `libgcrypt20` `1.10.1-3+deb12u1` | Package copyright notice, including LGPL-family material |
| [gdbm](https://sources.debian.org/src/gdbm/1.23-3/) | `libgdbm6` `1.23-3` | GPL-family notice in package copyright |
| [gmp](https://sources.debian.org/src/gmp/2%3A6.2.1+dfsg1-1.1/) | `libgmp10` `2:6.2.1+dfsg1-1.1` | LGPL-3.0/GPL-family notices in package copyright |
| [gnutls28](https://sources.debian.org/src/gnutls28/3.7.9-2+deb12u7/) | `libgnutls30` `3.7.9-2+deb12u7` | LGPL-2.1 and LGPL-3.0-or-GPL-2.0 notices in package copyright |
| [libgpg-error](https://sources.debian.org/src/libgpg-error/1.46-1/) | `libgpg-error0` `1.46-1` | GPL/LGPL notices in package copyright |
| [nettle](https://sources.debian.org/src/nettle/3.8.1-2/) | `libhogweed6`, `libnettle8` `3.8.1-2` | LGPL-family notices in package copyright |
| [libidn2](https://sources.debian.org/src/libidn2/2.3.3-1/) | `libidn2-0` binary `2.3.3-1+b1`; source `2.3.3-1` | GPL/LGPL notices in package copyright |
| [lz4](https://sources.debian.org/src/lz4/1.9.4-1/) | `liblz4-1` `1.9.4-1` | GPL-family material in package copyright |
| [xz-utils](https://snapshot.debian.org/package/xz-utils/5.4.1-1+deb12u2/) | `liblzma5` `5.4.1-1+deb12u2` | GPL/LGPL notices in package copyright |
| [libmd](https://sources.debian.org/src/libmd/1.0.4-2/) | `libmd0` `1.0.4-2` | GPL/LGPL notices in package copyright |
| [ncurses](https://sources.debian.org/src/ncurses/6.4-4/) | `libncursesw6`, `libtinfo6`, `ncurses-base`, `ncurses-bin` `6.4-4` | Mixed notices in package copyright |
| [p11-kit](https://sources.debian.org/src/p11-kit/0.24.1-2/) | `libp11-kit0` `0.24.1-2` | LGPL-2.1 notice in package copyright |
| [pam](https://sources.debian.org/src/pam/1.5.2-6+deb12u2/) | `libpam-modules`, `libpam-modules-bin`, `libpam-runtime`, `libpam0g` `1.5.2-6+deb12u2` | GPL/LGPL notices in package copyright |
| [pcre2](https://snapshot.debian.org/package/pcre2/10.42-1+deb12u1/) | `libpcre2-8-0` `10.42-1+deb12u1` | GPL-family material in package copyright |
| [readline](https://sources.debian.org/src/readline/8.2-1.3/) | `libreadline8`, `readline-common` `8.2-1.3` | GPL/GFDL notices in package copyright |
| [libseccomp](https://sources.debian.org/src/libseccomp/2.5.4-1+deb12u1/) | `libseccomp2` `2.5.4-1+deb12u1` | LGPL-2.1 notice in package copyright |
| [libselinux](https://sources.debian.org/src/libselinux/3.4-1/) | `libselinux1` binary `3.4-1+b6`; source `3.4-1` | GPL/LGPL notices in package copyright |
| [libsemanage](https://sources.debian.org/src/libsemanage/3.4-1/) | `libsemanage-common`, `libsemanage2` `3.4-1`/`3.4-1+b5`; source `3.4-1` | GPL/LGPL notices in package copyright |
| [libsepol](https://sources.debian.org/src/libsepol/3.4-2.1/) | `libsepol2` `3.4-2.1` | LGPL-2.1 notice in package copyright |
| [sqlite3](https://sources.debian.org/src/sqlite3/3.40.1-2+deb12u2/) | `libsqlite3-0` `3.40.1-2+deb12u2` | GPL/public-domain notices in package copyright |
| [openssl](https://snapshot.debian.org/package/openssl/3.0.22-1~deb12u1/) | `libssl3`, `openssl` `3.0.22-1~deb12u1` | GPL/Artistic and other notices in package copyright |
| [systemd](https://sources.debian.org/src/systemd/252.39-1~deb12u2/) | `libsystemd0`, `libudev1` `252.39-1~deb12u2` | LGPL/GPL notices in package copyright |
| [libtasn1-6](https://sources.debian.org/src/libtasn1-6/4.19.0-2+deb12u1/) | `libtasn1-6` `4.19.0-2+deb12u1` | LGPL-family notice in package copyright |
| [libunistring](https://sources.debian.org/src/libunistring/1.0-2/) | `libunistring2` `1.0-2` | LGPL-3.0-family notice in package copyright |
| [shadow](https://sources.debian.org/src/shadow/1%3A4.13+dfsg1-1+deb12u2/) | `login`, `passwd` `1:4.13+dfsg1-1+deb12u2` | Package copyright notice |
| [mawk](https://sources.debian.org/src/mawk/1.3.4.20200120-3.1/) | `mawk` `1.3.4.20200120-3.1` | GPL-2.0-family notice in package copyright |
| [netbase](https://sources.debian.org/src/netbase/6.4/) | `netbase` `6.4` | GPL-family notice in package copyright |
| [perl](https://sources.debian.org/src/perl/5.36.0-7+deb12u3/) | `perl-base` `5.36.0-7+deb12u3` | GPL/Artistic/LGPL notices in package copyright |
| [sed](https://sources.debian.org/src/sed/4.9-1+deb12u1/) | `sed` `4.9-1+deb12u1` | GPL/GFDL notices in package copyright |
| [sysvinit](https://sources.debian.org/src/sysvinit/3.06-4/) | `sysvinit-utils` `3.06-4` | GPL-family notice in package copyright |
| [tar](https://sources.debian.org/src/tar/1.34+dfsg-1.2+deb12u1/) | `tar` `1.34+dfsg-1.2+deb12u1` | GPL/LGPL notices in package copyright |
| [usrmerge](https://sources.debian.org/src/usrmerge/37~deb12u1/) | `usr-is-merged` `37~deb12u1` | GPL-family notice in package copyright |
| [libzstd](https://sources.debian.org/src/libzstd/1.5.4+dfsg2-5/) | `libzstd1` `1.5.4+dfsg2-5` | BSD/GPL notices in package copyright |
| [xxhash](https://sources.debian.org/src/xxhash/0.8.1-1/) | `libxxhash0` `0.8.1-1` | GPL-family material in package copyright |
| [zlib](https://sources.debian.org/src/zlib/1%3A1.2.13.dfsg-1/) | `zlib1g` `1:1.2.13.dfsg-1` | Package copyright notice |

## Python library source

| Library | License | Exact source |
|---|---|---|
| `zeroconf 0.151.5` | LGPL-2.1-or-later | [PyPI source archive](https://files.pythonhosted.org/packages/93/20/69744d9de9d375dae2639dda9add7dae85d52d690b68c61b23b8a6468434/zeroconf-0.151.5.tar.gz), SHA-256 `28c2ec9d772007eedf11b41a9c9fd3d5c684c17b00721ff8f1ee31b20ad286a1`; [project source](https://github.com/python-zeroconf/python-zeroconf/tree/0.151.5) |
| `certifi 2026.7.22` | MPL-2.0 | [PyPI source archive](https://files.pythonhosted.org/packages/a3/c2/24167ea9858356b47a87a50d39908bfdb72ceeefe0041586e704e5376b3a/certifi-2026.7.22.tar.gz), SHA-256 `741e2c3b351ddf169a738da9f2c048608ff7f2c5cc02f1ebc6b118bb090d5d55`; [project source](https://github.com/certifi/python-certifi/tree/f4bc676bc101fe2235846e37044e8c693d6cbaf4) |

## Release notices

1. Regenerate amd64 and arm64 SBOMs from the **final** ChannelWatch images and compare their Debian package inventories with this map. Update or remove any row whose final package version differs.
2. Archive the final image's `/usr/share/doc/*/copyright` files with the release source material. Keep the applicable full texts from `/usr/share/common-licenses`; package copyright files remain required for bespoke, dual, and exception licenses such as Sleepycat and the GCC Runtime Library Exception.
3. If distributing a selected SPDX text separately, use the pinned [SPDX license-list-data commit `5bf6d9610255540bfbee6890765a616042bf1e11`](https://github.com/spdx/license-list-data/tree/5bf6d9610255540bfbee6890765a616042bf1e11/text). The official raw `LGPL-3.0-only.txt` is [here](https://raw.githubusercontent.com/spdx/license-list-data/5bf6d9610255540bfbee6890765a616042bf1e11/text/LGPL-3.0-only.txt) (Git blob `513d1c01fe5b5b9724e999d8eb041ff17d444eaa`, SHA-256 `996af0513df21f7496288951c41428a03c174e9e4a9d63665c57d670f845ccb1`). The official raw `LGPL-2.0-only.txt` is [here](https://raw.githubusercontent.com/spdx/license-list-data/5bf6d9610255540bfbee6890765a616042bf1e11/text/LGPL-2.0-only.txt) (Git blob `843b00b561a44454249f6554c609b2899251b3fd`, SHA-256 `86dc99d7e5060915ab1dfc1378b7dd351c62088bfa74067e8aa1868c6fdba7d8`). The SPDX copy supplements the Debian notices; it does not replace them.
4. Include the Python source archive checksum above and retain the exact Dockerfile recipe commit. The official-image recipe is a build recipe, not a substitute for the verified Python source archive.
5. The unmodified `zeroconf 0.151.5` library uses the source archive and checksum listed above. You can replace it with a compatible version by changing its constraint and rebuilding the image.

## Rebuild and replacement

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

Check out the exact ChannelWatch release tag and build both target platforms with the pinned Python image index. A user can replace a dynamically linked LGPL component by rebuilding the container from the published Dockerfile and the documented source inputs. ChannelWatch does not prohibit compatible replacement of those components.

For a distribution-source request, identify the ChannelWatch release tag, image index digest, architecture, package, and package version.
