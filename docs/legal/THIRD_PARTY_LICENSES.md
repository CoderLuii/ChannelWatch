# Third-Party Licenses

ChannelWatch is built on open-source software. This file lists the major runtime dependencies, their versions, and their licenses. The dependency manifests in `deploy/requirements/runtime.txt`, `app/ui/package.json`, and `app/ui/pnpm-lock.yaml` are the authoritative dependency lists.

---

## Runtime Dependencies

| Package | Version (minimum) | License | Notes |
|---------|-------------------|---------|-------|
| [setuptools](https://github.com/pypa/setuptools) | >=82.0.1 | MIT | Python packaging |
| [pip](https://pip.pypa.io/) | >=26.1.2 | MIT | Package installer |
| [requests](https://requests.readthedocs.io/) | >=2.34.2 | Apache 2.0 | HTTP client |
| [httpx](https://www.python-httpx.org/) | >=0.28.1 | BSD 3-Clause | Async HTTP client |
| [pytz](https://pythonhosted.org/pytz/) | >=2026.2 | MIT | Timezone support |
| [pydantic](https://docs.pydantic.dev/) | >=2.13.4 | MIT | Data validation and settings |
| [SQLModel](https://sqlmodel.tiangolo.com/) | >=0.0.38 | MIT | SQLite models and persistence |
| [bcrypt](https://github.com/pyca/bcrypt/) | >=5.0.0 | Apache 2.0 | Password hashing |
| [cryptography](https://cryptography.io/) | >=49.0.0 | Apache 2.0 / BSD | Per-DVR API-key encryption and TLS helpers |
| [apprise](https://github.com/caronc/apprise) | >=1.11.0 | MIT | Multi-provider notification delivery |
| [fastapi](https://fastapi.tiangolo.com/) | >=0.138.0 | MIT | Web API framework |
| [uvicorn](https://uvicorn.dev/) | >=0.49.0 | BSD 3-Clause | ASGI server |
| [python-multipart](https://github.com/Kludex/python-multipart) | >=0.0.32 | Apache 2.0 | Multipart form parsing for FastAPI uploads |
| [zeroconf](https://github.com/python-zeroconf/python-zeroconf) | >=0.149.16 | LGPL 2.1 | mDNS/Bonjour DVR discovery |
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

Last verified: 2026-06-21 against `deploy/requirements/runtime.txt` and `app/ui/package.json`.
