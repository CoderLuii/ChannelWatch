# Third-Party Licenses

<!-- cspell:ignore httptools pycparser cffi -->

ChannelWatch is built on open-source software. This file lists the major runtime dependencies, their versions, and their licenses. The dependency manifests in `deploy/requirements/runtime.txt`, `app/ui/package.json`, and `app/ui/pnpm-lock.yaml` are the authoritative dependency lists.

---

## Runtime Dependencies

| Package | Version (minimum) | License | Notes |
|---------|-------------------|---------|-------|
| [setuptools](https://github.com/pypa/setuptools) | >=84.0.0 | MIT | Python packaging |
| [pip](https://pip.pypa.io/) | >=26.2.1 | MIT | Package installer |
| [requests](https://requests.readthedocs.io/) | >=2.34.2 | Apache 2.0 | HTTP client |
| [httpx](https://www.python-httpx.org/) | >=0.28.1 | BSD 3-Clause | Async HTTP client |
| [pytz](https://pythonhosted.org/pytz/) | >=2026.5 | MIT | Timezone support |
| [pydantic](https://docs.pydantic.dev/) | >=2.14.0 | MIT | Data validation and settings |
| [SQLModel](https://sqlmodel.tiangolo.com/) | >=0.0.48 | MIT | SQLite models and persistence |
| [bcrypt](https://github.com/pyca/bcrypt/) | >=5.0.0 | Apache 2.0 | Password hashing |
| [cryptography](https://cryptography.io/) | >=50.0.2 | Apache 2.0 / BSD | Per-DVR API-key encryption and TLS helpers |
| [apprise](https://github.com/caronc/apprise) | >=1.13.1, <2 | MIT | Multi-provider notification delivery |
| [oauthlib](https://github.com/oauthlib/oauthlib) | >=4.0.0 | BSD 3-Clause | OAuth request signing used by notification providers |
| [fastapi](https://fastapi.tiangolo.com/) | >=0.143.0 | MIT | Web API framework |
| [uvicorn](https://uvicorn.dev/) | >=0.54.0 | BSD 3-Clause | ASGI server |
| [python-multipart](https://github.com/Kludex/python-multipart) | >=0.0.32 | Apache 2.0 | Multipart form parsing for FastAPI uploads |
| [zeroconf](https://github.com/python-zeroconf/python-zeroconf) | >=0.151.5 | LGPL 2.1 | mDNS/Bonjour DVR discovery |
| [supervisor](http://supervisord.org/) | >=4.3.0 | BSD-derived (Repoze) | Process manager inside container |

### Runtime generation dependencies (selected)

The packages above pull in additional dependencies. This table records the exact versions in `deploy/requirements/runtime-update.lock` for selected runtime-generation packages and packages with notable licenses:

| Package | Locked version | License | Notes |
|---------|----------------|---------|-------|
| [anyio](https://anyio.readthedocs.io/) | 4.15.1 | MIT | Async compatibility layer |
| [certifi](https://github.com/certifi/python-certifi) | 2026.7.22 | MPL 2.0 | CA certificate bundle |
| [charset-normalizer](https://github.com/jawah/charset_normalizer) | 3.5.2 | MIT | Character encoding detection |
| [click](https://click.palletsprojects.com/) | 8.5.0 | BSD 3-Clause | CLI framework used by uvicorn |
| [h11](https://github.com/python-hyper/h11) | 0.16.0 | MIT | HTTP/1.1 implementation |
| [httptools](https://github.com/MagicStack/httptools) | 0.9.0 | MIT | HTTP protocol parser used by uvicorn |
| [idna](https://github.com/kjd/idna) | 3.20 | BSD-like | Internationalized domain names |
| [Markdown](https://github.com/Python-Markdown/markdown) | 3.11 | BSD 3-Clause | Markdown processing used by Apprise |
| [opentelemetry-api](https://github.com/open-telemetry/opentelemetry-python) | 1.45.1 | Apache 2.0 | Observability API used by FastAPI |
| [pycparser](https://github.com/eliben/pycparser) | 3.11 | BSD 3-Clause | C parser used by cffi |
| [python-dotenv](https://github.com/theskumar/python-dotenv) | 1.2.4 | BSD 3-Clause | Environment-file support used by uvicorn |
| [SQLAlchemy](https://github.com/sqlalchemy/sqlalchemy) | 2.1.4 | MIT | Database toolkit used by SQLModel |
| [starlette](https://www.starlette.io/) | 1.7.0 | BSD 3-Clause | ASGI toolkit used by FastAPI |
| [urllib3](https://urllib3.readthedocs.io/) | 2.8.0 | MIT | HTTP connection pooling |
| [websockets](https://github.com/python-websockets/websockets) | 17.2 | BSD 3-Clause | WebSocket implementation used by uvicorn |

The locked runtime generation contains 40 packages. SQLAlchemy 2.1.4 does not add `greenlet` to this closure.

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

The published image contains unmodified Debian Bookworm packages from the official Python 3.14.8 image. Both Python stages use image index `sha256:48b13b003dda20b16f9442b8475aa05fe21bf6579a8c881db92ffb4d8fd20f83`. The image retains Debian package copyright files under `/usr/share/doc` and full common-license texts under `/usr/share/common-licenses`.

The image and app-update archive include the complete GPL 1.0, GPL 2.0,
GPL 3.0, LGPL 2.0, LGPL 2.1, LGPL 3.0, and GCC Runtime Library Exception 3.1
texts under `licenses/copyleft`. The GitHub Release copyleft archive also
includes the final container's Debian notices and common-license texts.
Exact Debian source versions, the official Python build recipe and source
archive checksum, and rebuild/replacement guidance are recorded in
`docs/legal/CORRESPONDING_SOURCE.md`.

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

Inventory review: 2026-10-11 against `deploy/requirements/runtime.txt`, `deploy/requirements/runtime.constraints.txt`, `deploy/requirements/runtime-update.lock`, `app/ui/package.json`, the pinned container inputs above, and the refreshed runtime package inventory. Release SBOMs remain the authoritative exact inventory.
