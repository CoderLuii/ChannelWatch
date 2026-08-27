import type { ActivityClientFacet } from "@/lib/api"

export function normalizeActivityClientValue(value: string): string {
  return value.normalize("NFKC").trim().replace(/\s+/g, " ").toLocaleLowerCase()
}

export function canonicalActivityClientValue(
  clients: ActivityClientFacet[],
  value: string | null,
): string | null {
  if (!value) return null
  const normalized = normalizeActivityClientValue(value)
  return clients.find(
    (client) => normalizeActivityClientValue(client.value) === normalized,
  )?.value ?? null
}
