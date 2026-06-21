import catalog from "./en.json"

type Params = Record<string, string | number>

export function t(key: string, params?: Params): string {
  const raw = (catalog as Record<string, string>)[key] ?? key
  if (!params) return raw
  return raw.replace(/\{(\w+)\}/g, (_match, k) => String(params[k] ?? `{${k}}`))
}
