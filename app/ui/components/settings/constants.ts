import type { AppSettings, DVRServer } from "@/lib/types"

export const timezones = [
  "Africa/Abidjan",
  "Africa/Accra",
  "Africa/Algiers",
  "Africa/Bissau",
  "Africa/Cairo",
  "Africa/Casablanca",
  "Africa/Ceuta",
  "Africa/Dar_es_Salaam",
  "Africa/Johannesburg",
  "Africa/Khartoum",
  "Africa/Lagos",
  "Africa/Maputo",
  "Africa/Monrovia",
  "Africa/Nairobi",
  "Africa/Tripoli",
  "Africa/Tunis",
  "Africa/Windhoek",
  "America/Adak",
  "America/Anchorage",
  "America/Araguaina",
  "America/Argentina/Buenos_Aires",
  "America/Argentina/Cordoba",
  "America/Argentina/Salta",
  "America/Argentina/San_Juan",
  "America/Argentina/San_Luis",
  "America/Argentina/Tucuman",
  "America/Argentina/Ushuaia",
  "America/Asuncion",
  "America/Atikokan",
  "America/Bahia",
  "America/Bahia_Banderas",
  "America/Barbados",
  "America/Belem",
  "America/Belize",
  "America/Blanc-Sablon",
  "America/Bogota",
  "America/Boise",
  "America/Cambridge_Bay",
  "America/Campo_Grande",
  "America/Cancun",
  "America/Caracas",
  "America/Cayenne",
  "America/Chicago",
  "America/Chihuahua",
  "America/Costa_Rica",
  "America/Creston",
  "America/Cuiaba",
  "America/Danmarkshavn",
  "America/Dawson",
  "America/Dawson_Creek",
  "America/Denver",
  "America/Detroit",
  "America/Edmonton",
  "America/Eirunepe",
  "America/El_Salvador",
  "America/Fortaleza",
  "America/Glace_Bay",
  "America/Godthab",
  "America/Goose_Bay",
  "America/Grand_Turk",
  "America/Guatemala",
  "America/Guayaquil",
  "America/Guyana",
  "America/Halifax",
  "America/Havana",
  "America/Hermosillo",
  "America/Indiana/Indianapolis",
  "America/Indiana/Knox",
  "America/Indiana/Marengo",
  "America/Indiana/Petersburg",
  "America/Indiana/Tell_City",
  "America/Indiana/Vevay",
  "America/Indiana/Vincennes",
  "America/Indiana/Winamac",
  "America/Inuvik",
  "America/Iqaluit",
  "America/Jamaica",
  "America/Juneau",
  "America/Kentucky/Louisville",
  "America/Kentucky/Monticello",
  "America/La_Paz",
  "America/Lima",
  "America/Los_Angeles",
  "America/Maceio",
  "America/Managua",
  "America/Manaus",
  "America/Martinique",
  "America/Matamoros",
  "America/Mazatlan",
  "America/Menominee",
  "America/Merida",
  "America/Metlakatla",
  "America/Mexico_City",
  "America/Miquelon",
  "America/Moncton",
  "America/Monterrey",
  "America/Montevideo",
  "America/Montreal",
  "America/Nassau",
  "America/New_York",
  "America/Nipigon",
  "America/Nome",
  "America/Noronha",
  "America/North_Dakota/Beulah",
  "America/North_Dakota/Center",
  "America/North_Dakota/New_Salem",
  "America/Ojinaga",
  "America/Panama",
  "America/Pangnirtung",
  "America/Paramaribo",
  "America/Phoenix",
  "America/Port-au-Prince",
  "America/Port_of_Spain",
  "America/Porto_Velho",
  "America/Puerto_Rico",
  "America/Rainy_River",
  "America/Rankin_Inlet",
  "America/Recife",
  "America/Regina",
  "America/Resolute",
  "America/Rio_Branco",
  "America/Santa_Isabel",
  "America/Santarem",
  "America/Santiago",
  "America/Santo_Domingo",
  "America/Sao_Paulo",
  "America/Scoresbysund",
  "America/Sitka",
  "America/St_Johns",
  "America/Swift_Current",
  "America/Tegucigalpa",
  "America/Thule",
  "America/Thunder_Bay",
  "America/Tijuana",
  "America/Toronto",
  "America/Vancouver",
  "America/Whitehorse",
  "America/Winnipeg",
  "America/Yakutat",
  "America/Yellowknife",
  "Antarctica/Casey",
  "Antarctica/Davis",
  "Antarctica/DumontDUrville",
  "Antarctica/Macquarie",
  "Antarctica/Mawson",
  "Antarctica/Palmer",
  "Antarctica/Rothera",
  "Antarctica/Syowa",
  "Antarctica/Troll",
  "Antarctica/Vostok",
  "Asia/Almaty",
  "Asia/Amman",
  "Asia/Anadyr",
  "Asia/Aqtau",
  "Asia/Aqtobe",
  "Asia/Ashgabat",
  "Asia/Baghdad",
  "Asia/Bahrain",
  "Asia/Baku",
  "Asia/Bangkok",
  "Asia/Beirut",
  "Asia/Bishkek",
  "Asia/Brunei",
  "Asia/Chita",
  "Asia/Choibalsan",
  "Asia/Colombo",
  "Asia/Damascus",
  "Asia/Dhaka",
  "Asia/Dili",
  "Asia/Dubai",
  "Asia/Dushanbe",
  "Asia/Gaza",
  "Asia/Hebron",
  "Asia/Ho_Chi_Minh",
  "Asia/Hong_Kong",
  "Asia/Hovd",
  "Asia/Irkutsk",
  "Asia/Jakarta",
  "Asia/Jayapura",
  "Asia/Jerusalem",
  "Asia/Kabul",
  "Asia/Kamchatka",
  "Asia/Karachi",
  "Asia/Kathmandu",
  "Asia/Khandyga",
  "Asia/Kolkata",
  "Asia/Krasnoyarsk",
  "Asia/Kuala_Lumpur",
  "Asia/Kuching",
  "Asia/Kuwait",
  "Asia/Macau",
  "Asia/Magadan",
  "Asia/Makassar",
  "Asia/Manila",
  "Asia/Muscat",
  "Asia/Nicosia",
  "Asia/Novokuznetsk",
  "Asia/Novosibirsk",
  "Asia/Omsk",
  "Asia/Oral",
  "Asia/Phnom_Penh",
  "Asia/Pontianak",
  "Asia/Pyongyang",
  "Asia/Qatar",
  "Asia/Qyzylorda",
  "Asia/Rangoon",
  "Asia/Riyadh",
  "Asia/Sakhalin",
  "Asia/Samarkand",
  "Asia/Seoul",
  "Asia/Shanghai",
  "Asia/Singapore",
  "Asia/Srednekolymsk",
  "Asia/Taipei",
  "Asia/Tashkent",
  "Asia/Tbilisi",
  "Asia/Tehran",
  "Asia/Thimphu",
  "Asia/Tokyo",
  "Asia/Ulaanbaatar",
  "Asia/Urumqi",
  "Asia/Ust-Nera",
  "Asia/Vientiane",
  "Asia/Vladivostok",
  "Asia/Yakutsk",
  "Asia/Yekaterinburg",
  "Asia/Yerevan",
  "Atlantic/Azores",
  "Atlantic/Bermuda",
  "Atlantic/Canary",
  "Atlantic/Cape_Verde",
  "Atlantic/Faroe",
  "Atlantic/Madeira",
  "Atlantic/Reykjavik",
  "Atlantic/South_Georgia",
  "Atlantic/Stanley",
  "Australia/Adelaide",
  "Australia/Brisbane",
  "Australia/Broken_Hill",
  "Australia/Currie",
  "Australia/Darwin",
  "Australia/Eucla",
  "Australia/Hobart",
  "Australia/Lindeman",
  "Australia/Lord_Howe",
  "Australia/Melbourne",
  "Australia/Perth",
  "Australia/Sydney",
  "Canada/Atlantic",
  "Canada/Central",
  "Canada/Eastern",
  "Canada/Mountain",
  "Canada/Newfoundland",
  "Canada/Pacific",
  "Europe/Amsterdam",
  "Europe/Andorra",
  "Europe/Athens",
  "Europe/Belgrade",
  "Europe/Berlin",
  "Europe/Brussels",
  "Europe/Bucharest",
  "Europe/Budapest",
  "Europe/Chisinau",
  "Europe/Copenhagen",
  "Europe/Dublin",
  "Europe/Gibraltar",
  "Europe/Helsinki",
  "Europe/Istanbul",
  "Europe/Kaliningrad",
  "Europe/Kiev",
  "Europe/Lisbon",
  "Europe/London",
  "Europe/Luxembourg",
  "Europe/Madrid",
  "Europe/Malta",
  "Europe/Minsk",
  "Europe/Monaco",
  "Europe/Moscow",
  "Europe/Oslo",
  "Europe/Paris",
  "Europe/Prague",
  "Europe/Riga",
  "Europe/Rome",
  "Europe/Samara",
  "Europe/Simferopol",
  "Europe/Sofia",
  "Europe/Stockholm",
  "Europe/Tallinn",
  "Europe/Tirane",
  "Europe/Uzhgorod",
  "Europe/Vienna",
  "Europe/Vilnius",
  "Europe/Volgograd",
  "Europe/Warsaw",
  "Europe/Zaporozhye",
  "Europe/Zurich",
  "Indian/Antananarivo",
  "Indian/Chagos",
  "Indian/Christmas",
  "Indian/Cocos",
  "Indian/Comoro",
  "Indian/Kerguelen",
  "Indian/Mahe",
  "Indian/Maldives",
  "Indian/Mauritius",
  "Indian/Mayotte",
  "Indian/Reunion",
  "Pacific/Apia",
  "Pacific/Auckland",
  "Pacific/Bougainville",
  "Pacific/Chatham",
  "Pacific/Chuuk",
  "Pacific/Easter",
  "Pacific/Efate",
  "Pacific/Enderbury",
  "Pacific/Fakaofo",
  "Pacific/Fiji",
  "Pacific/Funafuti",
  "Pacific/Galapagos",
  "Pacific/Gambier",
  "Pacific/Guadalcanal",
  "Pacific/Guam",
  "Pacific/Honolulu",
  "Pacific/Kiritimati",
  "Pacific/Kosrae",
  "Pacific/Kwajalein",
  "Pacific/Majuro",
  "Pacific/Marquesas",
  "Pacific/Nauru",
  "Pacific/Niue",
  "Pacific/Norfolk",
  "Pacific/Noumea",
  "Pacific/Pago_Pago",
  "Pacific/Palau",
  "Pacific/Pitcairn",
  "Pacific/Pohnpei",
  "Pacific/Port_Moresby",
  "Pacific/Rarotonga",
  "Pacific/Tahiti",
  "Pacific/Tarawa",
  "Pacific/Tongatapu",
  "Pacific/Wake",
  "Pacific/Wallis",
  "US/Alaska",
  "US/Arizona",
  "US/Central",
  "US/Eastern",
  "US/Hawaii",
  "US/Mountain",
  "US/Pacific",
  "UTC",
]

type DiskAdvancedThresholdField =
  | "ds_warning_threshold_percent"
  | "ds_warning_threshold_gb"
  | "ds_critical_threshold_percent"
  | "ds_critical_threshold_gb"

type DiskAdvancedBehaviorField =
  | "ds_startup_grace_seconds"
  | "ds_worsening_delta_gb"
  | "ds_worsening_delta_percent"
  | "ds_alert_cooldown"

export const diskAdvancedThresholdFields: Array<{
  id: DiskAdvancedThresholdField
  label: string
  desc: string
  min: number
  max?: number
  step: number
  suffix: string
  placeholder: string
}> = [
  {
    id: "ds_warning_threshold_percent",
    label: "Warning percent",
    desc: "Warn when free space drops below this percent",
    min: 0,
    max: 100,
    step: 1,
    suffix: "%",
    placeholder: "10",
  },
  {
    id: "ds_warning_threshold_gb",
    label: "Warning GB",
    desc: "Warn when free space drops below this many gigabytes",
    min: 0,
    step: 1,
    suffix: "GB",
    placeholder: "50",
  },
  {
    id: "ds_critical_threshold_percent",
    label: "Critical percent",
    desc: "Critical when free space drops below this percent",
    min: 0,
    max: 100,
    step: 1,
    suffix: "%",
    placeholder: "5",
  },
  {
    id: "ds_critical_threshold_gb",
    label: "Critical GB",
    desc: "Critical when free space drops below this many gigabytes",
    min: 0,
    step: 1,
    suffix: "GB",
    placeholder: "25",
  },
]

export const diskAdvancedBehaviorFields: Array<{
  id: DiskAdvancedBehaviorField
  label: string
  desc?: string
  min: number
  max?: number
  step: number
  suffix: string
  placeholder: string
}> = [
  {
    id: "ds_startup_grace_seconds",
    label: "Startup delay",
    desc: "Wait this many seconds after startup before disk alerts can fire",
    min: 0,
    step: 1,
    suffix: "sec",
    placeholder: "10",
  },
  {
    id: "ds_worsening_delta_gb",
    label: "Repeat if free space drops by (GB)",
    min: 0,
    step: 1,
    suffix: "GB",
    placeholder: "1",
  },
  {
    id: "ds_worsening_delta_percent",
    label: "Repeat if free space drops by (%)",
    desc: "Either one triggers a repeat.",
    min: 0,
    max: 100,
    step: 0.1,
    suffix: "%",
    placeholder: "1.0",
  },
  {
    id: "ds_alert_cooldown",
    label: "Disk alert cooldown",
    desc: "Minimum time between repeat low-disk notifications",
    min: 0,
    step: 1,
    suffix: "sec",
    placeholder: "3600",
  },
]

const newWebhookId = () => {
  const randomUuid = globalThis.crypto?.randomUUID?.bind(globalThis.crypto)
  return randomUuid
    ? `webhook_${randomUuid()}`
    : `webhook_${Date.now()}_${Math.random().toString(36).slice(2)}`
}

export const emptyWebhookEntry = (): AppSettings["webhooks"][number] => ({
  id: newWebhookId(),
  url: "",
  secret: "",
  enabled: true,
})

type DiskAdvancedField = DiskAdvancedThresholdField | DiskAdvancedBehaviorField

// An emptied number input is kept as NaN while editing so it is never saved
// as 0. On save a cleared global field restores its default and a cleared
// per-DVR override is dropped, so that DVR goes back to the global value.
export function parseNumberFieldInput(raw: string): number {
  if (raw.trim() === "") return Number.NaN
  const parsed = Number(raw)
  return Number.isFinite(parsed) ? parsed : Number.NaN
}

export function numberFieldInputValue(value: unknown): number | "" {
  return typeof value === "number" && Number.isFinite(value) ? value : ""
}

export function isClearedNumberField(value: unknown): boolean {
  return typeof value === "number" && !Number.isFinite(value)
}

function diskThresholdError(values: Record<string, unknown>, suffix: string): string | null {
  const warnPct = values.ds_warning_threshold_percent as number
  const critPct = values.ds_critical_threshold_percent as number
  const warnGb = values.ds_warning_threshold_gb as number
  const critGb = values.ds_critical_threshold_gb as number
  if (critPct > 0 && warnPct > 0 && critPct >= warnPct) return `Critical percent must be lower than warning percent${suffix}.`
  if (critGb > 0 && warnGb > 0 && critGb >= warnGb) return `Critical GB must be lower than warning GB${suffix}.`
  return null
}

type DvrServerOverrides = Pick<DVRServer, "id" | "name" | "host" | "overrides" | "deleted_at">

export function normalizeDiskAdvancedSettings<T extends Partial<Record<DiskAdvancedField, unknown>> & { dvr_servers?: DvrServerOverrides[] }>(
  settings: T,
): { settings: T; error: string | null; errorDvrId: string | null } {
  const next: Record<string, unknown> = { ...settings }
  const fields = [...diskAdvancedThresholdFields, ...diskAdvancedBehaviorFields]
  for (const field of fields) {
    const value = next[field.id]
    if (typeof value !== "number" || !Number.isFinite(value)) {
      next[field.id] = Number(field.placeholder)
    }
  }
  const servers = (settings.dvr_servers ?? []).map((server) => {
    if (!server.overrides) return server
    const overrides = Object.fromEntries(Object.entries(server.overrides).filter(([, value]) => !isClearedNumberField(value)))
    return { ...server, overrides }
  })
  if (settings.dvr_servers) next.dvr_servers = servers

  let error = diskThresholdError(next, "")
  let errorDvrId: string | null = null
  if (!error) {
    for (const server of servers) {
      if (server.deleted_at) continue
      const suffix = ` on ${server.name || server.host || "this DVR"}`
      // Global values count too: an override can conflict with what the DVR inherits.
      error = diskThresholdError({ ...next, ...server.overrides }, suffix)
      for (const field of fields) {
        if (error) break
        const value = server.overrides?.[field.id]
        if (typeof value !== "number") continue
        if (value < field.min || (field.max !== undefined && value > field.max)) {
          error = field.max !== undefined
            ? `${field.label} must be between ${field.min} and ${field.max}${suffix}.`
            : `${field.label} must be ${field.min} or more${suffix}.`
        }
      }
      if (error) {
        errorDvrId = server.id
        break
      }
    }
  }
  return { settings: next as T, error, errorDvrId }
}
