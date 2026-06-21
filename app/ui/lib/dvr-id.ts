/**
 * Canonical DVR id utility — browser-compatible TypeScript.
 *
 * Contract: "dvr_" + md5(normalized_host + ":" + port)[:8]
 * Must produce byte-identical output to app/core/helpers/dvr_id.py for the same inputs.
 *
 * IPv6 normalization: strip surrounding [ ], lowercase.
 * IPv4 and hostname inputs are hashed as-is (case-preserving).
 *
 * The MD5 implementation below is a pure-JS port of the RFC 1321 algorithm,
 * equivalent to Python hashlib.md5 for ASCII input (all valid host:port strings).
 */

// Addition modulo 2^32 without sign-extension hazards.
function add32(a: number, b: number): number {
  return (a + b) >>> 0;
}

function leftRotate(x: number, n: number): number {
  return ((x << n) | (x >>> (32 - n))) >>> 0;
}

function md5Round(
  fn: (b: number, c: number, d: number) => number,
  a: number, b: number, c: number, d: number,
  x: number, s: number, t: number,
): number {
  return add32(leftRotate(add32(add32(a, fn(b, c, d)), add32(x >>> 0, t >>> 0)), s), b);
}

function md5Hex(input: string): string {
  // Encode as UTF-8 bytes. All host:port chars are ASCII so charCode == UTF-8 byte.
  const bytes: number[] = [];
  for (let i = 0; i < input.length; i++) {
    const cp = input.charCodeAt(i);
    if (cp < 0x80) {
      bytes.push(cp);
    } else if (cp < 0x800) {
      bytes.push(0xc0 | (cp >> 6), 0x80 | (cp & 0x3f));
    } else {
      bytes.push(0xe0 | (cp >> 12), 0x80 | ((cp >> 6) & 0x3f), 0x80 | (cp & 0x3f));
    }
  }

  const msgLen = bytes.length;
  bytes.push(0x80);
  while ((bytes.length % 64) !== 56) bytes.push(0);

  // Append message length in bits as 64-bit little-endian.
  const bitLen = msgLen * 8;
  for (let i = 0; i < 4; i++) bytes.push((bitLen >>> (i * 8)) & 0xff);
  for (let i = 0; i < 4; i++) bytes.push(0);

  // Build 32-bit little-endian word array.
  const words = new Uint32Array(bytes.length / 4);
  for (let i = 0; i < bytes.length; i++) {
    words[i >>> 2] |= bytes[i] << ((i & 3) * 8);
  }

  let a0 = 0x67452301;
  let b0 = 0xefcdab89;
  let c0 = 0x98badcfe;
  let d0 = 0x10325476;

  // Precomputed table: T[i] = floor(abs(sin(i+1)) * 2^32)
  const T = [
    0xd76aa478, 0xe8c7b756, 0x242070db, 0xc1bdceee,
    0xf57c0faf, 0x4787c62a, 0xa8304613, 0xfd469501,
    0x698098d8, 0x8b44f7af, 0xffff5bb1, 0x895cd7be,
    0x6b901122, 0xfd987193, 0xa679438e, 0x49b40821,
    0xf61e2562, 0xc040b340, 0x265e5a51, 0xe9b6c7aa,
    0xd62f105d, 0x02441453, 0xd8a1e681, 0xe7d3fbc8,
    0x21e1cde6, 0xc33707d6, 0xf4d50d87, 0x455a14ed,
    0xa9e3e905, 0xfcefa3f8, 0x676f02d9, 0x8d2a4c8a,
    0xfffa3942, 0x8771f681, 0x6d9d6122, 0xfde5380c,
    0xa4beea44, 0x4bdecfa9, 0xf6bb4b60, 0xbebfbc70,
    0x289b7ec6, 0xeaa127fa, 0xd4ef3085, 0x04881d05,
    0xd9d4d039, 0xe6db99e5, 0x1fa27cf8, 0xc4ac5665,
    0xf4292244, 0x432aff97, 0xab9423a7, 0xfc93a039,
    0x655b59c3, 0x8f0ccc92, 0xffeff47d, 0x85845dd1,
    0x6fa87e4f, 0xfe2ce6e0, 0xa3014314, 0x4e0811a1,
    0xf7537e82, 0xbd3af235, 0x2ad7d2bb, 0xeb86d391,
  ];

  const S = [
    7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22,
    5,  9, 14, 20, 5,  9, 14, 20, 5,  9, 14, 20, 5,  9, 14, 20,
    4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23,
    6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21,
  ];

  for (let chunk = 0; chunk < words.length; chunk += 16) {
    let A = a0, B = b0, C = c0, D = d0;

    for (let i = 0; i < 64; i++) {
      let F: number, g: number;
      if (i < 16) {
        F = (B & C) | (~B & D);
        g = i;
      } else if (i < 32) {
        F = (D & B) | (~D & C);
        g = (5 * i + 1) % 16;
      } else if (i < 48) {
        F = B ^ C ^ D;
        g = (3 * i + 5) % 16;
      } else {
        F = C ^ (B | ~D);
        g = (7 * i) % 16;
      }
      const temp = D;
      D = C;
      C = B;
      B = md5Round(() => F, A, B, C, D, words[chunk + g], S[i], T[i]);
      A = temp;
    }

    a0 = add32(a0, A);
    b0 = add32(b0, B);
    c0 = add32(c0, C);
    d0 = add32(d0, D);
  }

  function word2hex(w: number): string {
    let hex = "";
    for (let i = 0; i < 4; i++) {
      hex += ((w >>> (i * 8)) & 0xff).toString(16).padStart(2, "0");
    }
    return hex;
  }

  return word2hex(a0) + word2hex(b0) + word2hex(c0) + word2hex(d0);
}

function normalizeHost(host: string): string {
  const stripped = host.replace(/^\[|]$/g, "");
  return stripped.includes(":") ? stripped.toLowerCase() : stripped;
}

export function canonicalDvrId(host: string, port: number): string {
  const normalized = normalizeHost(host);
  const digest = md5Hex(`${normalized}:${port}`);
  return "dvr_" + digest.slice(0, 8);
}
