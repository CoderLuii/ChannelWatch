# Third-Party Licenses

ChannelWatch is built on open-source software. This file lists the major runtime dependencies, their versions, and their licenses. The dependency manifests in `deploy/requirements/runtime.txt`, `app/ui/package.json`, and `app/ui/pnpm-lock.yaml` are the authoritative dependency lists.

---

## Runtime Dependencies

| Package | Version (minimum) | License | Notes |
|---------|-------------------|---------|-------|
| [setuptools](https://github.com/pypa/setuptools) | >=84.0.0 | MIT | Python packaging |
| [pip](https://pip.pypa.io/) | >=26.2.1 | MIT | Package installer |
| [requests](https://requests.readthedocs.io/) | >=2.34.2 | Apache 2.0 | HTTP client |
| [httpx](https://www.python-httpx.org/) | >=0.28.1 | BSD 3-Clause | Async HTTP client |
| [pytz](https://pythonhosted.org/pytz/) | >=2026.3.post1 | MIT | Timezone support |
| [pydantic](https://docs.pydantic.dev/) | >=2.13.4 | MIT | Data validation and settings |
| [SQLModel](https://sqlmodel.tiangolo.com/) | >=0.0.39 | MIT | SQLite models and persistence |
| [bcrypt](https://github.com/pyca/bcrypt/) | >=5.0.0 | Apache 2.0 | Password hashing |
| [cryptography](https://cryptography.io/) | >=50.0.0 | Apache 2.0 / BSD | Per-DVR API-key encryption and TLS helpers |
| [apprise](https://github.com/caronc/apprise) | >=1.12.0 | MIT | Multi-provider notification delivery |
| [fastapi](https://fastapi.tiangolo.com/) | >=0.141.1 | MIT | Web API framework |
| [uvicorn](https://uvicorn.dev/) | >=0.52.1 | BSD 3-Clause | ASGI server |
| [python-multipart](https://github.com/Kludex/python-multipart) | >=0.0.32 | Apache 2.0 | Multipart form parsing for FastAPI uploads |
| [zeroconf](https://github.com/python-zeroconf/python-zeroconf) | >=0.150.0 | LGPL 2.1 | mDNS/Bonjour DVR discovery |
| [supervisor](http://supervisord.org/) | >=4.3.0 | BSD-derived (Repoze) | Process manager inside container |

### Transitive dependencies (selected)

The packages above pull in additional transitive dependencies. Key ones with notable licenses:

| Package | License | Notes |
|---------|---------|-------|
| [starlette](https://www.starlette.io/) | BSD 3-Clause | ASGI toolkit (FastAPI dependency) |
| [anyio](https://anyio.readthedocs.io/) | MIT | Async compatibility layer |
| [certifi](https://github.com/certifi/python-certifi) | MPL 2.0 | CA certificate bundle |
| [charset-normalizer](https://github.com/Ousret/charset_normalizer) | MIT | Character encoding detection |
| [idna](https://github.com/kjd/idna) | BSD-like | Internationalized domain names |
| [urllib3](https://urllib3.readthedocs.io/) | MIT | HTTP connection pooling |
| [click](https://click.palletsprojects.com/) | BSD 3-Clause | CLI framework (uvicorn) |
| [h11](https://github.com/python-hyper/h11) | MIT | HTTP/1.1 implementation |
| [sniffio](https://github.com/python-trio/sniffio) | MIT / Apache 2.0 | Async library detection |

---

## Frontend Dependencies (Next.js UI)

The web UI is built with Next.js and React. Key frontend dependencies:

| Package | License | Notes |
|---------|---------|-------|
| [Next.js](https://nextjs.org/) | MIT | React framework |
| [React](https://react.dev/) | MIT | UI library |
| [Tailwind CSS](https://tailwindcss.com/) | MIT | Utility-first CSS |
| [shadcn/ui](https://ui.shadcn.com/) | MIT | UI component library |
| [Radix UI](https://www.radix-ui.com/) | MIT | Accessible UI primitives |
| [Lucide React](https://lucide.dev/) | ISC | Icon library |

A full frontend dependency list is available in `app/ui/package.json` and `app/ui/pnpm-lock.yaml`. The current dependency manifest also includes React Hook Form, Recharts, Zod, class-variance-authority, clsx, cmdk, next-themes, tailwind-merge, tw-animate-css, and build/test tooling such as TypeScript, Vitest, Playwright, ESLint, PostCSS, and Vite.

---

## LGPL Notice (zeroconf)

`zeroconf` is licensed under the **GNU Lesser General Public License v2.1 (LGPL 2.1)**. ChannelWatch uses it as an unmodified library dependency. The LGPL 2.1 requires that users be able to replace the library with a modified version. This is satisfied by:

1. The ChannelWatch source code being available on GitHub.
2. The `deploy/docker/Dockerfile` being included in the repository, allowing users to rebuild the image with a modified `zeroconf` version.

No modifications have been made to the `zeroconf` library itself.

## Container base packages

The published image also contains unmodified operating-system packages from the pinned Chainguard Python base image. The v0.9.17 SBOM and license scan identify GPL or LGPL metadata for `gdbm`, `glibc`, `ld-linux`, `libuuid`, `libzstd`, `readline`, and `xz`. These packages are not copied into the ChannelWatch source tree, and their package metadata remains in the image.

The reviewed v0.9.17 container inputs are the multi-architecture Chainguard Python runtime index `sha256:1f6779775c9f466890da563e411cb677045a6c20b6a65160eefad1deffb5012c`, the build-only Python development index `sha256:4bf7e945777010672b8ccd5d2ae2c41c91ad6d3478878347c731ae536d506bef`, and Wolfi package `tzdata=2026c-r0`. The Dockerfile pins each value so the final image can be reproduced and its package sources traced through the SBOM package identifiers and Chainguard package repositories.

The image and app-update archive include the complete GPL 1.0, GPL 2.0,
GPL 3.0, LGPL 2.1, and GCC Runtime Library Exception 3.1 texts identified by
the v0.9.17 SBOM under `licenses/copyleft`. The matching GitHub Release also
attaches those texts in a single archive. Exact upstream commits, source
archive digests, Wolfi recipes and patches, and rebuild/replacement guidance
are recorded in `docs/legal/CORRESPONDING_SOURCE.md`.

Release maintainers must preserve the applicable notices and source-availability obligations when distributing the container. The SPDX and CycloneDX SBOMs generated from the final image are the authoritative release-specific package inventory; this hand-maintained file is an explanatory summary rather than a substitute for those artifacts.

The publication owner must review the exact-image license report, corresponding-source availability, and notices before distribution. This project documentation records the technical inventory and rebuild path; it is not a legal opinion.

---

## Apache 2.0 Notice

Several dependencies are licensed under the Apache License, Version 2.0. The `docs/legal/NOTICE` file in this repository satisfies the attribution requirement for those dependencies. A copy of the Apache 2.0 license is available at:

https://www.apache.org/licenses/LICENSE-2.0

---

## License Policy Summary

ChannelWatch avoids the following license categories for runtime code dependencies:

- AGPL (any version)
- SSPL
- Non-commercial / "free for non-commercial use" variants
- Creative Commons licenses on code (CC-BY-NC, etc.)

Dependencies with one of these licenses need to be replaced or reviewed before they are added to the runtime surface.

---

Last verified: 2026-08-23 against `deploy/requirements/runtime.txt`, `deploy/requirements/runtime.constraints.txt`, `app/ui/package.json`, the pinned container inputs above, and the v0.9.17 candidate image SBOM.
