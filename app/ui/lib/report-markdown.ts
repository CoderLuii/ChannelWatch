import type { ReportDiagnostics } from "@/lib/api"

export function escapeMarkdownTableCell(value: unknown): string {
  return String(value ?? "")
    .replace(/\\/g, "\\\\")
    .replace(/\|/g, "\\|")
    .replace(/\r\n|\r|\n/g, "\\n")
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, " ")
    .replace(/[ \t]+/g, " ")
    .trim()
}

const IPV6_CANDIDATE_RE = /(?:(?:[0-9a-f]{1,4}:){2,}[0-9a-f:.]*|[0-9a-f]{1,4}::[0-9a-f:.]*|::[0-9a-f:.]+)(?:%[a-z0-9_.~-]+)?(?:\/\d{1,3})?/gi

function parseIpv4Octets(value: string): number[] | null {
  const octets = value.split(".").map(Number)
  return octets.length === 4 && octets.every((octet) => Number.isInteger(octet) && octet >= 0 && octet <= 255)
    ? octets
    : null
}

function isPrivateIpv4(octets: number[]): boolean {
  return octets[0] === 10 || octets[0] === 127 ||
    (octets[0] === 169 && octets[1] === 254) ||
    (octets[0] === 172 && octets[1] >= 16 && octets[1] <= 31) ||
    (octets[0] === 192 && octets[1] === 168)
}

function parseIpv6Hextets(value: string): number[] | null {
  let address = value.toLowerCase().replace(/\/\d{1,3}$/, "").split("%", 1)[0]
  if (address.includes(".")) {
    const splitAt = address.lastIndexOf(":")
    const octets = parseIpv4Octets(address.slice(splitAt + 1))
    if (splitAt < 0 || !octets) return null
    address = `${address.slice(0, splitAt)}:${((octets[0] << 8) | octets[1]).toString(16)}:${((octets[2] << 8) | octets[3]).toString(16)}`
  }
  if ((address.match(/::/g) || []).length > 1) return null
  const compressed = address.includes("::")
  const [leftText, rightText = ""] = address.split("::")
  const left = leftText ? leftText.split(":") : []
  const right = rightText ? rightText.split(":") : []
  if (compressed && left.length + right.length >= 8) return null
  const parts = compressed ? [...left, ...Array(8 - left.length - right.length).fill("0"), ...right] : left
  if (parts.length !== 8 || parts.some((part) => !/^[0-9a-f]{1,4}$/.test(part))) return null
  return parts.map((part) => Number.parseInt(part, 16))
}

function classifyPrivateIpv6(value: string): boolean | null {
  const hextets = parseIpv6Hextets(value)
  if (!hextets) return null
  if ((hextets[0] & 0xfe00) === 0xfc00 || (hextets[0] & 0xffc0) === 0xfe80) return true
  if (hextets.slice(0, 7).every((part) => part === 0) && hextets[7] === 1) return true
  if (hextets.slice(0, 5).every((part) => part === 0) && hextets[5] === 0xffff) {
    return isPrivateIpv4([hextets[6] >>> 8, hextets[6] & 0xff, hextets[7] >>> 8, hextets[7] & 0xff])
  }
  return false
}

function isPrivateIpv6(value: string): boolean {
  return classifyPrivateIpv6(value) === true
}

function redactPrivateIpv6Candidate(candidate: string): string {
  let address = candidate
  let punctuation = ""
  while (address) {
    const classification = classifyPrivateIpv6(address)
    if (classification !== null) {
      return classification ? `[redacted-private-address]${punctuation}` : candidate
    }
    if (!/[.:]$/.test(address)) return candidate
    punctuation = address.slice(-1) + punctuation
    address = address.slice(0, -1)
  }
  return candidate
}

function redactStandaloneBasic(match: string, encoded: string): string {
  try {
    const decoded = atob(encoded)
    const separator = decoded.indexOf(":")
    return separator >= 0 ? "[redacted-credential]" : match
  } catch {
    return match
  }
}

function isPrivateHostname(hostname: string): boolean {
  const normalized = hostname.toLowerCase().replace(/^\[|\]$/g, "").replace(/\.$/, "").split("%", 1)[0]
  if (normalized === "localhost" || normalized === "::1" || normalized.endsWith(".local")) {
    return true
  }
  if (normalized.includes(":")) return isPrivateIpv6(normalized)
  const octets = parseIpv4Octets(normalized)
  return octets ? isPrivateIpv4(octets) : false
}

function isSensitiveQueryKey(key: string): boolean {
  let decoded: string
  try {
    decoded = decodeURIComponent(key.replace(/\+/g, " "))
  } catch {
    return true
  }
  const normalized = decoded.toLowerCase().replace(/[^a-z0-9]/g, "")
  return ["code", "key", "policy", "expires"].includes(normalized) || isSensitiveStructuredKey(decoded)
}

function redactSensitiveQueryFragments(value: string): string {
  return value.replace(/([?&;])([^=&;#\s"'`,}\])]+)=([^&;#\s"'`,}\])]*?)(?=$|[&;#\s"'`,}\])])/g,
    (match, prefix, key, rawValue) => {
      if (key.length <= 256 && !isSensitiveQueryKey(key)) return match
      const suffix = /[.!?]+$/.exec(rawValue)?.[0] ?? ""
      return `${prefix}${key}=[redacted]${suffix}`
    })
}

function sanitizePublicUrl(raw: string): string {
  let punctuation = ""
  while (/[.!]$/.test(raw)) {
    punctuation = raw.slice(-1) + punctuation
    raw = raw.slice(0, -1)
  }
  try {
    const url = new URL(raw)
    if (url.username || url.password || isPrivateHostname(url.hostname)) {
      return `[redacted-private-url]${punctuation}`
    }
    for (const key of [...url.searchParams.keys()]) {
      if (isSensitiveQueryKey(key)) {
        url.searchParams.set(key, "[redacted]")
      }
    }
    url.hash = ""
    return url.toString().replace(/%5Bredacted%5D/gi, "[redacted]") + punctuation
  } catch {
    return `[redacted-url]${punctuation}`
  }
}

const STRUCTURED_SENSITIVE_KEY = "api[_ -]?key|access[_ -]?token|refresh[_ -]?token|client[_ -]?secret|private[_ -]?key|authorization|credential|token|secret|password|passwd|webhook|dsn"
const SENSITIVE_STRUCTURED_COMPONENTS = new Set([
  "apikey", "accesstoken", "refreshtoken", "clientsecret", "privatekey", "signingkey", "webhookurl",
  "accesskeyid", "awsaccesskeyid", "googleaccessid", "keypairid", "sig", "signature",
  "authorization", "auth", "credential", "credentials", "token", "tokens", "secret", "secrets",
  "password", "passwd", "webhook", "dsn", "cookie", "cookies", "session", "sessions",
])
const SENSITIVE_STRUCTURED_PHRASES = [
  ["access", "key"], ["access", "id"], ["account", "key"], ["key", "pair", "id"], ["secret", "key"],
  ["api", "key"], ["private", "key"], ["signing", "key"], ["access", "token"],
  ["refresh", "token"], ["client", "secret"],
]
const SAFE_STRUCTURED_METADATA_SUFFIXES = new Set(["count", "counts", "policy", "policies"])
const SENSITIVE_FUSED_STRUCTURED_SUFFIXES = [
  "keypairid", "apikey", "privatekey", "signingkey", "webhookurl", "dsn",
  "accesskeyid", "accessid", "accesskey", "accountkey", "secretkey", "authkey", "sessionid", "signature",
  "credentials", "credential", "password", "passwd", "secret", "token",
]
const MAX_EMBEDDED_JSON_CANDIDATES = 64
const MAX_EMBEDDED_JSON_CHARS = 64 * 1024

function findBalancedValueEnd(value: string, start: number): number | null {
  const opening = value[start]
  const closing = opening === "{" ? "}" : opening === "[" ? "]" : null
  if (!closing) return null
  let depth = 0
  let quote = ""
  let escaped = false
  for (let index = start; index < value.length; index += 1) {
    const character = value[index]
    if (quote) {
      if (escaped) escaped = false
      else if (character === "\\") escaped = true
      else if (character === quote) quote = ""
      continue
    }
    if (character === "\"" || character === "'") quote = character
    else if (character === opening) depth += 1
    else if (character === closing && --depth === 0) return index + 1
  }
  return null
}

function indentedContinuationEnd(value: string, lineEnd: number, baseIndent: number): number {
  if (lineEnd >= value.length) return lineEnd
  let cursor = lineEnd + 1
  let end = lineEnd
  while (cursor <= value.length) {
    const newline = value.indexOf("\n", cursor)
    const nextEnd = newline < 0 ? value.length : newline
    const line = value.slice(cursor, nextEnd).replace(/\r$/, "")
    const indent = line.length - line.replace(/^[ \t]*/, "").length
    if (line.trim() && indent <= baseIndent) break
    end = nextEnd
    if (nextEnd >= value.length) break
    cursor = nextEnd + 1
  }
  return end
}

function indentationlessSequenceEnd(value: string, lineEnd: number, baseIndent: number): number {
  if (lineEnd >= value.length) return lineEnd
  let cursor = lineEnd + 1
  let end = lineEnd
  let pendingEnd = lineEnd
  let sawSequence = false
  while (cursor <= value.length) {
    const newline = value.indexOf("\n", cursor)
    const nextEnd = newline < 0 ? value.length : newline
    const line = value.slice(cursor, nextEnd).replace(/\r$/, "")
    const stripped = line.replace(/^[ \t]*/, "")
    const indent = line.length - stripped.length
    if (indent === 0 && (stripped === "---" || stripped === "...")) break
    if (!stripped || stripped.startsWith("#")) pendingEnd = nextEnd
    else if (indent === baseIndent && /^-(?:[ \t]|$)/.test(stripped)) {
      sawSequence = true
      end = nextEnd
    } else if (sawSequence && indent > baseIndent) end = nextEnd
    else break
    if (sawSequence && pendingEnd > end) end = pendingEnd
    if (nextEnd >= value.length) break
    cursor = nextEnd + 1
  }
  return sawSequence ? end : lineEnd
}

function stripYamlNodeProperties(value: string): { value: string; offset: number; found: boolean } {
  let cursor = 0
  let found = false
  while (cursor < value.length) {
    let tokenStart = cursor
    while (tokenStart < value.length && /[ \t]/.test(value[tokenStart])) tokenStart += 1
    if (tokenStart >= value.length || !/[!&]/.test(value[tokenStart])) break
    let tokenEnd: number
    if (value.startsWith("!<", tokenStart)) {
      const closing = value.indexOf(">", tokenStart + 2)
      if (closing < 0) return { value: "", offset: value.length, found: true }
      tokenEnd = closing + 1
    } else {
      tokenEnd = tokenStart + 1
      while (tokenEnd < value.length && !/[ \t]/.test(value[tokenEnd])) tokenEnd += 1
    }
    if (tokenEnd === tokenStart + 1) break
    found = true
    cursor = tokenEnd
  }
  while (cursor < value.length && /[ \t]/.test(value[cursor])) cursor += 1
  if (value.startsWith("#", cursor)) return { value: "", offset: value.length, found }
  return { value: value.slice(cursor), offset: cursor, found }
}

function isSensitiveStructuredKey(value: string): boolean {
  const separated = value
    .replace(/([A-Z]+)([A-Z][a-z])/g, "$1 $2")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
  const components = separated.match(/[A-Za-z0-9]+/g)?.map((component) => component.toLowerCase()) ?? []
  const compact = components.join("")
  if (!components.length || SAFE_STRUCTURED_METADATA_SUFFIXES.has(components.at(-1)!)
      || (components.length === 1
        && [...SAFE_STRUCTURED_METADATA_SUFFIXES].some((suffix) => compact.endsWith(suffix)))) return false
  if (components.some((component) => SENSITIVE_STRUCTURED_COMPONENTS.has(component))) return true
  if (components.length === 1
      && SENSITIVE_FUSED_STRUCTURED_SUFFIXES.some((suffix) => compact.endsWith(suffix))) return true
  return SENSITIVE_STRUCTURED_PHRASES.some((phrase) => components.some(
    (_component, index) => phrase.every((part, offset) => components[index + offset] === part),
  ))
}

function decodeYamlDoubleQuotedKey(value: string): string | null {
  if (value.length > 512) return null
  const escapes: Record<string, string> = {
    "0": "\0", a: "\x07", b: "\b", t: "\t", n: "\n", v: "\v", f: "\f", r: "\r", e: "\x1b",
    " ": " ", '"': '"', "/": "/", "\\": "\\", N: "\u0085", _: "\u00a0", L: "\u2028", P: "\u2029",
  }
  let decoded = ""
  for (let cursor = 0; cursor < value.length;) {
    const character = value[cursor]
    if (character !== "\\") {
      if (character.charCodeAt(0) < 0x20) return null
      decoded += character
      cursor += 1
      continue
    }
    if (cursor + 1 >= value.length) return null
    const escape = value[cursor + 1]
    if (Object.hasOwn(escapes, escape)) {
      decoded += escapes[escape]
      cursor += 2
      continue
    }
    const width = escape === "x" ? 2 : escape === "u" ? 4 : escape === "U" ? 8 : 0
    if (!width || cursor + 2 + width > value.length) return null
    const digits = value.slice(cursor + 2, cursor + 2 + width)
    if (!/^[0-9A-Fa-f]+$/.test(digits)) return null
    const codepoint = Number.parseInt(digits, 16)
    if (codepoint > 0x10ffff || (codepoint >= 0xd800 && codepoint <= 0xdfff)) return null
    decoded += String.fromCodePoint(codepoint)
    cursor += 2 + width
  }
  return decoded
}

function decodeYamlQuotedKey(value: string): { value: string | null; malformed: boolean } {
  if (value.startsWith('"')) {
    if (value.length < 2 || !value.endsWith('"')) return { value: null, malformed: true }
    const decoded = decodeYamlDoubleQuotedKey(value.slice(1, -1))
    return { value: decoded, malformed: decoded === null }
  }
  if (value.startsWith("'")) {
    if (value.length < 2 || !value.endsWith("'") || value.length > 512) return { value: null, malformed: true }
    const inner = value.slice(1, -1)
    let decoded = ""
    for (let cursor = 0; cursor < inner.length;) {
      if (inner[cursor] !== "'") {
        decoded += inner[cursor]
        cursor += 1
      } else if (cursor + 1 < inner.length && inner[cursor + 1] === "'") {
        decoded += "'"
        cursor += 2
      } else return { value: null, malformed: true }
    }
    return { value: decoded, malformed: false }
  }
  return { value, malformed: false }
}

function isSensitiveYamlExplicitKey(value: string): boolean {
  const semantic = stripYamlNodeProperties(value.trim()).value.replace(/[ \t]+#[^\r\n]*$/, "").trim()
  const decoded = decodeYamlQuotedKey(semantic)
  return decoded.malformed || (decoded.value !== null && isSensitiveStructuredKey(decoded.value))
}

function structuredAssignmentIsSensitive(match: RegExpExecArray): boolean {
  const quoted = match[1] || match[2]
  const decoded = quoted ? decodeYamlQuotedKey(quoted) : { value: match[3] ?? "", malformed: false }
  if (decoded.malformed || decoded.value === null) return true
  if (match[5]) {
    const separated = decoded.value
      .replace(/([A-Z]+)([A-Z][a-z])/g, "$1 $2")
      .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    const components = separated.match(/[A-Za-z0-9]+/g)?.map((component) => component.toLowerCase()) ?? []
    const compositeShape = /[_./-]|[a-z0-9][A-Z]/.test(decoded.value)
    const fusedShape = components.length === 1
      && !SENSITIVE_STRUCTURED_COMPONENTS.has(components[0])
      && SENSITIVE_FUSED_STRUCTURED_SUFFIXES.some((suffix) => components[0].endsWith(suffix))
    if (!compositeShape && !fusedShape) return false
  }
  return isSensitiveStructuredKey(decoded.value)
}

function explicitYamlValueEnd(value: string, rhsStart: number, lineEnd: number, baseIndent: number): number {
  const rhs = value.slice(rhsStart, lineEnd).replace(/\r$/, "")
  const strippedRhs = rhs.trimStart()
  const valueStart = rhsStart + rhs.length - strippedRhs.length
  const semantic = stripYamlNodeProperties(strippedRhs)
  const semanticStart = valueStart + semantic.offset
  if (semantic.value.startsWith("{") || semantic.value.startsWith("[")) {
    return findBalancedValueEnd(value, semanticStart) ?? value.length
  }
  const sequenceEnd = semantic.value ? lineEnd : indentationlessSequenceEnd(value, lineEnd, baseIndent)
  const continuationEnd = Math.max(indentedContinuationEnd(value, lineEnd, baseIndent), sequenceEnd)
  const yamlMarker = /^(?:[|>][+-]?\d?|[|>]\d?[+-]?)$/.test(semantic.value)
  if (yamlMarker || continuationEnd > lineEnd || !semantic.value || semantic.found) {
    return continuationEnd > lineEnd ? continuationEnd : lineEnd
  }
  return lineEnd
}

function redactExplicitYamlSensitiveValues(input: string): string {
  let value = input
  const keyLine = /^([ \t]*)\?([^\r\n]*)(?:\r?\n|$)/gm
  let cursor = 0
  while (cursor < value.length) {
    keyLine.lastIndex = cursor
    const match = keyLine.exec(value)
    if (!match) break
    const baseIndent = match[1].length
    const tail = match[2].trim()
    let keyEnd = keyLine.lastIndex
    let sensitive = Boolean(tail && !tail.startsWith("#") && isSensitiveYamlExplicitKey(tail))
    if (!tail || tail.startsWith("#")) {
      let keyCursor = keyLine.lastIndex
      while (keyCursor <= value.length) {
        const newline = value.indexOf("\n", keyCursor)
        const nextEnd = newline < 0 ? value.length : newline
        const line = value.slice(keyCursor, nextEnd).replace(/\r$/, "")
        const stripped = line.trimStart()
        const indent = line.length - stripped.length
        if (!stripped || stripped.startsWith("#")) {
          if (nextEnd >= value.length) break
          keyCursor = nextEnd + 1
          continue
        }
        if (indent > baseIndent) {
          sensitive = isSensitiveYamlExplicitKey(stripped)
          keyEnd = nextEnd + (nextEnd < value.length ? 1 : 0)
        }
        break
      }
    }
    if (!sensitive) {
      cursor = keyLine.lastIndex
      continue
    }
    let lineCursor = keyEnd
    let rhsStart: number | null = null
    let lineEnd = value.length
    while (lineCursor <= value.length) {
      const newline = value.indexOf("\n", lineCursor)
      const nextEnd = newline < 0 ? value.length : newline
      const line = value.slice(lineCursor, nextEnd).replace(/\r$/, "")
      const stripped = line.trimStart()
      const indent = line.length - stripped.length
      if (!stripped || stripped.startsWith("#")) {
        if (nextEnd >= value.length) break
        lineCursor = nextEnd + 1
        continue
      }
      if (indent === baseIndent && /^:(?:[ \t]|$)/.test(stripped)) {
        let colonOffset = indent + 1
        while (colonOffset < line.length && /[ \t]/.test(line[colonOffset])) colonOffset += 1
        rhsStart = lineCursor + colonOffset
        lineEnd = nextEnd
      }
      break
    }
    const start = match.index + baseIndent
    const end = rhsStart === null ? value.length : explicitYamlValueEnd(value, rhsStart, lineEnd, baseIndent)
    value = `${value.slice(0, start)}[redacted-structured-data]${value.slice(end)}`
    cursor = start + "[redacted-structured-data]".length
  }
  return value
}

function redactCompleteJson(value: string): string | null {
  const trimmed = value.trim()
  if (!((trimmed.startsWith("{") && trimmed.endsWith("}")) || (trimmed.startsWith("[") && trimmed.endsWith("]")))) {
    return null
  }
  let parsed: unknown
  try {
    parsed = JSON.parse(trimmed)
  } catch {
    return null
  }
  const redact = (node: unknown): [unknown, boolean] => {
    if (Array.isArray(node)) {
      let changed = false
      const result = node.map((child) => {
        const [redactedChild, childChanged] = redact(child)
        changed ||= childChanged
        return redactedChild
      })
      return [result, changed]
    }
    if (node !== null && typeof node === "object") {
      let changed = false
      const entries = Object.entries(node).map(([key, child]): [string, unknown] => {
        if (isSensitiveStructuredKey(key)) {
          changed = true
          return [key, "[redacted]"]
        }
        const [redactedChild, childChanged] = redact(child)
        changed ||= childChanged
        return [key, redactedChild]
      })
      return [Object.fromEntries(entries), changed]
    }
    if (typeof node === "string") {
      const redactedString = redactStructuredSensitiveValues(node)
      return [redactedString, redactedString !== node]
    }
    return [node, false]
  }
  const [redacted, changed] = redact(parsed)
  if (changed && parsed !== null && !Array.isArray(parsed) && typeof parsed === "object"
      && Object.keys(parsed).every((key) => isSensitiveStructuredKey(key))) {
    return "[redacted-structured-data]"
  }
  return changed ? JSON.stringify(redacted) : value
}

function redactEmbeddedJson(input: string): string {
  let value = input
  let cursor = 0
  let attempts = 0
  while (cursor < value.length) {
    const objectStart = value.indexOf("{", cursor)
    const arrayStart = value.indexOf("[", cursor)
    const starts = [objectStart, arrayStart].filter((index) => index >= 0)
    if (!starts.length) break
    const start = Math.min(...starts)
    attempts += 1
    if (attempts > MAX_EMBEDDED_JSON_CANDIDATES) return `${value.slice(0, start)}[redacted-structured-data]`
    const end = findBalancedValueEnd(value, start)
    if (end === null) {
      cursor = start + 1
      continue
    }
    if (end - start > MAX_EMBEDDED_JSON_CHARS) {
      value = `${value.slice(0, start)}[redacted-structured-data]${value.slice(end)}`
      cursor = start + "[redacted-structured-data]".length
      continue
    }
    const candidate = value.slice(start, end)
    const redacted = redactCompleteJson(candidate)
    if (redacted === null) {
      cursor = start + 1
      continue
    }
    if (redacted !== candidate) {
      value = `${value.slice(0, start)}${redacted}${value.slice(end)}`
      cursor = start + redacted.length
    } else cursor = end
  }
  return value
}

function xmlTokenEnd(value: string, start: number): number | null {
  let quote = ""
  for (let index = start + 1; index < value.length; index += 1) {
    const character = value[index]
    if (quote) {
      if (character === quote) quote = ""
    } else if (character === "\"" || character === "'") quote = character
    else if (character === ">") return index + 1
  }
  return null
}

function nextSensitiveXmlOpen(value: string, initialCursor: number): { start: number; end: number; tag: string } | null {
  let cursor = initialCursor
  while (cursor < value.length) {
    const tokenStart = value.indexOf("<", cursor)
    if (tokenStart < 0) return null
    if (value.startsWith("<!--", tokenStart)) {
      const end = value.indexOf("-->", tokenStart + 4)
      if (end < 0) return null
      cursor = end + 3
      continue
    }
    if (value.startsWith("<![CDATA[", tokenStart)) {
      const end = value.indexOf("]]>", tokenStart + 9)
      if (end < 0) return null
      cursor = end + 3
      continue
    }
    if (value.startsWith("<?", tokenStart)) {
      const end = value.indexOf("?>", tokenStart + 2)
      if (end < 0) return null
      cursor = end + 2
      continue
    }
    const tokenEnd = xmlTokenEnd(value, tokenStart)
    if (tokenEnd === null) return null
    const name = /^<\s*([A-Za-z_][\w.:-]*)/.exec(value.slice(tokenStart, tokenEnd))?.[1]
    if (name && isSensitiveStructuredKey(name.split(":").at(-1)!)) return { start: tokenStart, end: tokenEnd, tag: name }
    cursor = tokenEnd
  }
  return null
}

function sensitiveXmlElementEnd(value: string, start: number, openingEnd: number, tag: string): number {
  if (/\/\s*>$/.test(value.slice(start, openingEnd))) return openingEnd
  let depth = 1
  let cursor = openingEnd
  while (cursor < value.length) {
    const tokenStart = value.indexOf("<", cursor)
    if (tokenStart < 0) return value.length
    if (value.startsWith("<!--", tokenStart)) {
      const end = value.indexOf("-->", tokenStart + 4)
      cursor = end < 0 ? value.length : end + 3
      continue
    }
    if (value.startsWith("<![CDATA[", tokenStart)) {
      const end = value.indexOf("]]>", tokenStart + 9)
      cursor = end < 0 ? value.length : end + 3
      continue
    }
    if (value.startsWith("<?", tokenStart)) {
      const end = value.indexOf("?>", tokenStart + 2)
      cursor = end < 0 ? value.length : end + 2
      continue
    }
    const tokenEnd = xmlTokenEnd(value, tokenStart)
    if (tokenEnd === null) return value.length
    const token = value.slice(tokenStart, tokenEnd)
    const name = /^<\s*(\/?)\s*([A-Za-z_][\w.:-]*)/.exec(token)
    if (name?.[2] === tag) {
      if (name[1]) {
        depth -= 1
        if (depth === 0) return tokenEnd
      } else if (!/\/\s*>$/.test(token)) depth += 1
    }
    cursor = tokenEnd
  }
  return value.length
}

function heredocTerminatorEnd(value: string, bodyStart: number, delimiter: string, allowIndent: boolean): number | null {
  let cursor = bodyStart
  while (cursor <= value.length) {
    const newline = value.indexOf("\n", cursor)
    const lineEnd = newline < 0 ? value.length : newline
    const line = value.slice(cursor, lineEnd).replace(/\r$/, "")
    const candidate = allowIndent ? line.trim() : line.replace(/[ \t]+$/, "")
    if (candidate === delimiter) return lineEnd
    if (lineEnd >= value.length) return null
    cursor = lineEnd + 1
  }
  return null
}

function redactSensitiveXml(value: string): string {
  let cursor = 0
  while (cursor < value.length) {
    const opening = nextSensitiveXmlOpen(value, cursor)
    if (!opening) break
    const end = sensitiveXmlElementEnd(value, opening.start, opening.end, opening.tag)
    value = `${value.slice(0, opening.start)}[redacted-structured-data]${value.slice(end)}`
    cursor = opening.start + "[redacted-structured-data]".length
  }
  return value
}

function redactStructuredSensitiveValues(input: string): string {
  const parsedJson = redactCompleteJson(input)
  if (parsedJson !== null) return parsedJson
  let value = redactEmbeddedJson(input)
  value = redactSensitiveQueryFragments(value)
  value = redactSensitiveXml(value)
  value = redactExplicitYamlSensitiveValues(value)
  const trimmed = value.trim()
  const rootContainer = (trimmed.startsWith("{") && trimmed.endsWith("}")) || (trimmed.startsWith("[") && trimmed.endsWith("]"))
  const assignment = new RegExp(`(?<![\\w-])(?:("(?:\\\\.|[^"\\\\\\r\\n])*")|('(?:''|[^'\\r\\n])*')|([A-Za-z][A-Za-z0-9_./-]{0,127}))(?:[ \\t]*(=>|:=|[:=])[ \\t]*|[ \\t]+(is)[ \\t]+)`, "ig")
  let cursor = 0
  while (cursor < value.length) {
    assignment.lastIndex = cursor
    const match = assignment.exec(value)
    if (!match) break
    if (!structuredAssignmentIsSensitive(match)) {
      cursor = assignment.lastIndex
      continue
    }
    const rhsStart = assignment.lastIndex
    const existingRedaction = ['"[redacted]"', "'[redacted]'", "[redacted]"]
      .find((marker) => value.startsWith(marker, rhsStart))
    if (existingRedaction) {
      cursor = rhsStart + existingRedaction.length
      continue
    }
    const lineStart = value.lastIndexOf("\n", match.index - 1) + 1
    const newline = value.indexOf("\n", rhsStart)
    const lineEnd = newline < 0 ? value.length : newline
    const rhs = value.slice(rhsStart, lineEnd).replace(/\r$/, "")
    const strippedRhs = rhs.trimStart()
    const valueStart = rhsStart + rhs.length - strippedRhs.length
    const prefix = value.slice(lineStart, match.index)
    if (/\b(?:proxy-authorization|authorization|set-cookie|cookie)\s*[:=]/i.test(prefix)) {
      cursor = assignment.lastIndex
      continue
    }
    const baseIndent = prefix.length - prefix.trimStart().length
    const structuralLine = /^[ \t]*(?:-[ \t]+)?$/.test(prefix)
    const legacyScalarKey = new RegExp(`^(?:${STRUCTURED_SENSITIVE_KEY}|proxy-authorization|set-cookie|cookie)$`, "i")
    const operator = (match[4] || match[5] || "").toLowerCase()
    const credentialPrefix = /^(?:Bearer|Basic|Token)[ \t]+/i.test(strippedRhs)
    const inlineAssignment = operator !== ":" || credentialPrefix
    const needsCompositeScalarRedaction = structuralLine && (!match[3] || !legacyScalarKey.test(match[3])) && !inlineAssignment
    const compositeKey = !match[3] || !legacyScalarKey.test(match[3])
    let end: number | null = null
    let multiline = false
    const semantic = stripYamlNodeProperties(strippedRhs)
    const semanticStart = valueStart + semantic.offset
    const triple = semantic.value.startsWith('"""') ? '"""' : semantic.value.startsWith("'''") ? "'''" : null

    const heredoc = /^<<(-?)([A-Za-z_][\w-]*)[ \t]*$/.exec(semantic.value)
    if (heredoc) {
      end = heredocTerminatorEnd(value, lineEnd + 1, heredoc[2], Boolean(heredoc[1])) ?? value.length
      multiline = true
    } else if (triple) {
      const closing = value.indexOf(triple, semanticStart + 3)
      end = closing < 0 ? value.length : closing + 3
      multiline = value.slice(semanticStart, end).includes("\n")
    } else if (semantic.value.startsWith("{") || semantic.value.startsWith("[")) {
      end = findBalancedValueEnd(value, semanticStart)
      multiline = end === null || value.slice(semanticStart, end).includes("\n")
      end ??= value.length
    } else {
      const sequenceEnd = semantic.value ? lineEnd : indentationlessSequenceEnd(value, lineEnd, baseIndent)
      const continuationEnd = Math.max(indentedContinuationEnd(value, lineEnd, baseIndent), sequenceEnd)
      const hasContinuation = continuationEnd > lineEnd && structuralLine
      const yamlMarker = /^(?:[|>][+-]?\d?|[|>]\d?[+-]?)$/.test(semantic.value)
      if (yamlMarker || hasContinuation || !semantic.value) {
        end = hasContinuation ? continuationEnd : lineEnd
        multiline = hasContinuation
      } else if (semantic.found || needsCompositeScalarRedaction) end = lineEnd
    }

    if (end === null) {
      if (compositeKey && (!structuralLine || inlineAssignment)) {
        let inlineEnd: number | null = null
        if (strippedRhs.startsWith('"')) {
          const quoted = /^"(?:\\.|[^"\\])*"/.exec(strippedRhs)
          inlineEnd = quoted ? valueStart + quoted[0].length : lineEnd
        } else if (strippedRhs.startsWith("'")) {
          const quoted = /^'(?:''|[^'])*'/.exec(strippedRhs)
          inlineEnd = quoted ? valueStart + quoted[0].length : lineEnd
        } else {
          const token = /^(?:Bearer|Basic|Token)[ \t]+[^\s,;&]+|^[^\s,;&]+/i.exec(strippedRhs)
          inlineEnd = token ? valueStart + token[0].length : null
          while (inlineEnd !== null && inlineEnd > valueStart && /[.!?]/.test(value[inlineEnd - 1])) inlineEnd -= 1
        }
        if (inlineEnd !== null && inlineEnd > valueStart) {
          value = `${value.slice(0, valueStart)}[redacted]${value.slice(inlineEnd)}`
          cursor = valueStart + "[redacted]".length
          continue
        }
      }
      cursor = assignment.lastIndex
      continue
    }
    if (rootContainer && multiline) return "[redacted-structured-data]"
    value = `${value.slice(0, match.index)}[redacted-structured-data]${value.slice(end)}`
    cursor = match.index + "[redacted-structured-data]".length
  }
  return value
}

export function redactPublicText(value: string): string {
  const safeValue = redactStructuredSensitiveValues(value)
  return safeValue
    .replace(/^[ \t]{0,3}\[[^\]\r\n]+\]:[^\r\n]*(?:\r?\n|$)/gm, "")
    .replace(/!\[([^\]]*)\]\s*\[[^\]]*\]/g, "$1 [image removed]")
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1 [image removed]")
    .replace(/!\[([^\]]+)\](?!\s*[\[(])/g, "$1 [image removed]")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/https?:\/\/[^\s<>()"'`,}]+/gi, (url) => sanitizePublicUrl(url))
    .replace(/"[^"\r\n]+"@[^\s<>()]+|[^\s<>()@]+@[^\s<>()]+/gi, "[redacted-email]")
    .replace(/(['"])(api[_ -]?key|access[_ -]?token|refresh[_ -]?token|client[_ -]?secret|private[_ -]?key|authorization|credential|token|secret|password|passwd|webhook|dsn)\1\s*:\s*(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\[[^\]\r\n]*\]|\{[^}\r\n]*\}|[^,}\r\n]+)/gi, (_match, quote, key) => `${quote}${key}${quote}:"[redacted]"`)
    .replace(/\b(api[_ -]?key|access[_ -]?token|refresh[_ -]?token|client[_ -]?secret|private[_ -]?key|authorization|credential|token|secret|password|passwd|webhook|dsn)\b\s*(?:(?:is)\b\s*|[:=]\s*)(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')/gi, (_match, key) => `${key}=[redacted]`)
    .replace(/<(api[_ -]?key|access[_ -]?token|refresh[_ -]?token|client[_ -]?secret|private[_ -]?key|authorization|credential|token|secret|password|passwd|webhook|dsn)>[^<\r\n]*<\/\1>/gi, (_match, key) => `<${key}>[redacted]</${key}>`)
    .replace(/(['"])\b(proxy-authorization|authorization|set-cookie|cookie)\s*:[^\r\n]*?\1/gi, (_match, quote, key) => `${quote}${key}=[redacted]${quote}`)
    .replace(/(['"])\b(proxy-authorization|authorization|set-cookie|cookie)\s*:[^\r\n]*$/gim, (_match, quote, key) => `${quote}${key}=[redacted]`)
    .replace(/(^|[^'"])\b(proxy-authorization|authorization|set-cookie|cookie)\s*[:=][^\r\n]*/gim, (_match, prefix, key) => `${prefix}${key}=[redacted]`)
    .replace(
      /\b(api[\s_-]*key|access[\s_-]*token|refresh[\s_-]*token|client[\s_-]*secret|private[\s_-]*key|authorization|credential|token|secret|password|passwd|webhook|dsn)\b\s*(?:(?:is)\b\s*|[:=]\s*)(?:(?:bearer|basic|token)\s+)?(?!\[redacted\])([^\s,;]+)/gi,
      (_match, key) => `${key}=[redacted]`,
    )
    .replace(/\bbearer\s+[^\s,;]+/gi, "bearer=[redacted]")
    .replace(
      /\bbasic\s+((?:[a-z0-9+/]{4})*(?:[a-z0-9+/]{4}|[a-z0-9+/]{2}==|[a-z0-9+/]{3}=))(?=$|[\s,;.!?'"])/gi,
      redactStandaloneBasic,
    )
    .replace(IPV6_CANDIDATE_RE, redactPrivateIpv6Candidate)
    .replace(/\b(?:10|127)\.(?:\d{1,3}\.){2}\d{1,3}\b/g, "[redacted-private-address]")
    .replace(/\b169\.254\.(?:\d{1,3}\.)\d{1,3}\b/g, "[redacted-private-address]")
    .replace(/\b172\.(?:1[6-9]|2\d|3[01])\.(?:\d{1,3}\.)\d{1,3}\b/g, "[redacted-private-address]")
    .replace(/\b192\.168\.(?:\d{1,3}\.)\d{1,3}\b/g, "[redacted-private-address]")
    .replace(/\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b/g, "[redacted-secret]")
    .replace(/\b[A-Za-z0-9_-]{32,}\b/g, "[redacted-secret]")
    .replace(/<|>/g, (character) => (character === "<" ? "&lt;" : "&gt;"))
    .replace(/@(?=[A-Za-z0-9_-])/g, "@\u200b")
    .replace(/#(?=\d)/g, "#\u200b")
}

export function publicDiagnosticValue(value: unknown, fallback: string): string {
  return redactPublicText(String(value ?? "").replace(/\s+/g, " ").trim() || fallback).replace(
    /https?:\/\/[^\s<>()]+/gi,
    "[redacted-url]",
  )
}

export function publicDiagnosticsRows(diagnostics: ReportDiagnostics): Array<[string, string]> {
  const enabled = [
    diagnostics.feature_toggles.channel_watching ? "Channel watching" : null,
    diagnostics.feature_toggles.vod_watching ? "VOD" : null,
    diagnostics.feature_toggles.disk_space ? "Disk space" : null,
    diagnostics.feature_toggles.recording_events ? "Recordings" : null,
    diagnostics.feature_toggles.stream_counter ? "Stream counter" : null,
  ].filter(Boolean)
  return [
    ["Version", publicDiagnosticValue(diagnostics.channelwatch_version, "Unknown")],
    ["DVRs", `${diagnostics.connected_dvr_count} connected of ${diagnostics.dvr_count}`],
    ["Core", publicDiagnosticValue(diagnostics.core_status, "Unknown")],
    ["Monitoring", publicDiagnosticValue(diagnostics.monitoring_statuses.join(", "), "Not reported")],
    ["Providers", publicDiagnosticValue(diagnostics.notification_providers.join(", "), "None reported")],
    ["Feature toggles", publicDiagnosticValue(enabled.join(", "), "None reported")],
  ]
}
