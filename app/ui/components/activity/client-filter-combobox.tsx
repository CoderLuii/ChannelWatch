"use client"

import { useState } from "react"
import { Check, ChevronsUpDown, Loader2 } from "lucide-react"

import { Button } from "@/components/base/button"
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/base/command"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/base/popover"
import type { ActivityClientFacet } from "@/lib/api"
import { t } from "@/lib/i18n"
import { cn } from "@/lib/utils"

interface ClientFilterComboboxProps {
  clients: ActivityClientFacet[]
  value: string | null
  onChange: (value: string | null) => void
  loading?: boolean
  disabled?: boolean
  compact?: boolean
  ariaLabel?: string
}

export function ClientFilterCombobox({
  clients,
  value,
  onChange,
  loading = false,
  disabled = false,
  compact = false,
  ariaLabel = t("activity.client"),
}: ClientFilterComboboxProps) {
  const [open, setOpen] = useState(false)
  const selected = clients.find((client) => client.value === value) ?? null

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="outline"
          role="combobox"
          aria-expanded={open}
          aria-label={ariaLabel}
          disabled={disabled}
          className={cn(
            "min-h-11 justify-between gap-2 font-normal",
            compact ? "h-8 min-h-8 max-w-44 px-2 text-xs" : "w-full",
          )}
        >
          <span className="min-w-0 truncate" title={selected?.label}>
            {selected?.label ?? t("activity.allClients")}
          </span>
          {loading ? <Loader2 className="h-4 w-4 shrink-0 animate-spin" /> : <ChevronsUpDown className="h-4 w-4 shrink-0 opacity-50" />}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-[min(22rem,calc(100vw-2rem))] p-0">
        <Command>
          <CommandInput placeholder={t("activity.searchClients")} />
          <CommandList>
            <CommandEmpty>{t("activity.noClients")}</CommandEmpty>
            <CommandGroup>
              <CommandItem
                value={t("activity.allClients")}
                onSelect={() => {
                  onChange(null)
                  setOpen(false)
                }}
                className="min-h-11"
              >
                <Check className={cn("h-4 w-4", value === null ? "opacity-100" : "opacity-0")} />
                <span>{t("activity.allClients")}</span>
              </CommandItem>
              {clients.map((client) => (
                <CommandItem
                  key={client.value}
                  value={`${client.label} ${client.value}`}
                  onSelect={() => {
                    onChange(client.value)
                    setOpen(false)
                  }}
                  className="min-h-11"
                >
                  <Check className={cn("h-4 w-4", value === client.value ? "opacity-100" : "opacity-0")} />
                  <span className="min-w-0 flex-1 truncate" title={client.label}>{client.label}</span>
                  <span className="text-xs tabular-nums text-muted-foreground">{client.count}</span>
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}
