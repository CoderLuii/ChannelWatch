import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it, expect } from "vitest";
import { canonicalDvrId } from "../lib/dvr-id";

interface Vector {
  input: { host: string; port: number };
  expected_id: string;
}

const VECTORS: Vector[] = JSON.parse(
  readFileSync(join(__dirname, "dvr-id-vectors.json"), "utf-8"),
);

describe("canonicalDvrId — IPv4 and hostname", () => {
  it.each([
    ["192.168.1.1", 8089, "dvr_aef6d698"],
    ["192.168.1.2", 8089, "dvr_56a5e213"],
    ["10.0.0.1",    8089, "dvr_e6785710"],
    ["dvr.local",   8089, "dvr_e1b92638"],
    ["dvr.example.com", 8089, "dvr_89cc9b63"],
    ["localhost",   8089, "dvr_e6f9a23f"],
    ["127.0.0.1",   8089, "dvr_db3313ef"],
    ["0.0.0.0",     8089, "dvr_c7d9568a"],
    ["192.168.100.200", 8089, "dvr_493f41ca"],
  ] as [string, number, string][])("%s:%d → %s", (host, port, expected) => {
    expect(canonicalDvrId(host, port)).toBe(expected);
  });
});

describe("canonicalDvrId — IPv6 normalization", () => {
  it.each([
    ["::1",                            8089, "dvr_6244d64f"],
    ["[::1]",                          8089, "dvr_6244d64f"],
    ["2001:db8::1",                    8089, "dvr_2e679e72"],
    ["[2001:db8::1]",                  8089, "dvr_2e679e72"],
    ["2001:DB8::1",                    8089, "dvr_2e679e72"],
    ["[2001:DB8::1]",                  8089, "dvr_2e679e72"],
    ["fe80::1%eth0",                   8089, "dvr_f356cadc"],
    ["[fe80::1%eth0]",                 8089, "dvr_f356cadc"],
    ["2001:db8:85a3::8a2e:370:7334",   8089, "dvr_e5a54470"],
    ["[2001:db8:85a3::8a2e:370:7334]", 8089, "dvr_e5a54470"],
  ] as [string, number, string][])("%s:%d → %s", (host, port, expected) => {
    expect(canonicalDvrId(host, port)).toBe(expected);
  });
});

describe("canonicalDvrId — port variation", () => {
  it("different ports on same host produce different ids", () => {
    const a = canonicalDvrId("192.168.1.1", 8089);
    const b = canonicalDvrId("192.168.1.1", 9090);
    expect(a).toBe("dvr_aef6d698");
    expect(b).toBe("dvr_b3033482");
    expect(a).not.toBe(b);
  });

  it("non-standard port dvr.local:57000", () => {
    expect(canonicalDvrId("dvr.local", 57000)).toBe("dvr_836678c7");
  });

  it("IPv6 loopback with non-standard port", () => {
    expect(canonicalDvrId("::1", 1234)).toBe("dvr_4b60e4e7");
  });
});

describe("canonicalDvrId — format", () => {
  it("output starts with dvr_", () => {
    expect(canonicalDvrId("192.168.1.1", 8089)).toMatch(/^dvr_/);
  });

  it("output is exactly 12 characters", () => {
    expect(canonicalDvrId("192.168.1.1", 8089)).toHaveLength(12);
  });

  it("output is deterministic", () => {
    expect(canonicalDvrId("dvr.local", 8089)).toBe(canonicalDvrId("dvr.local", 8089));
  });

  it("different hosts produce different ids", () => {
    expect(canonicalDvrId("192.168.1.1", 8089)).not.toBe(canonicalDvrId("192.168.1.2", 8089));
  });
});

describe("canonicalDvrId — hostname case behaviour", () => {
  it("uppercase hostname is NOT normalized (case-preserving)", () => {
    const upper = canonicalDvrId("CHANNELSDVR.LOCAL", 8089);
    const lower = canonicalDvrId("channelsdvr.local", 8089);
    expect(upper).toBe("dvr_6ed91433");
    expect(upper).not.toBe(lower);
  });
});

describe("canonicalDvrId — IPv6 equivalence invariants", () => {
  it("bracketed and unbracketed ::1 are equal", () => {
    expect(canonicalDvrId("::1", 8089)).toBe(canonicalDvrId("[::1]", 8089));
  });

  it("uppercase and lowercase IPv6 are equal", () => {
    expect(canonicalDvrId("2001:DB8::1", 8089)).toBe(canonicalDvrId("2001:db8::1", 8089));
  });

  it("bracketed uppercase and unbracketed lowercase IPv6 are equal", () => {
    expect(canonicalDvrId("[2001:DB8::1]", 8089)).toBe(canonicalDvrId("2001:db8::1", 8089));
  });

  it("zone id bracketed and unbracketed are equal", () => {
    expect(canonicalDvrId("[fe80::1%eth0]", 8089)).toBe(canonicalDvrId("fe80::1%eth0", 8089));
  });
});

describe("cross-language consistency — shared vector fixture", () => {
  it("fixture has at least 20 vectors", () => {
    expect(VECTORS.length).toBeGreaterThanOrEqual(20);
  });

  it.each(VECTORS)("$input.host:$input.port → $expected_id", ({ input, expected_id }) => {
    expect(canonicalDvrId(input.host, input.port)).toBe(expected_id);
  });
});
