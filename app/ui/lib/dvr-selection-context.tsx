"use client"

import { createContext, useCallback, useContext, useEffect, useState } from "react"
import type { DVRServer } from "@/lib/types"

const LS_KEY = "cw.selected_dvr"

export interface DvrSelectionContextValue {
  /** "all" for aggregate mode, or a dvr_id string for per-DVR mode */
  selectedDvr: string
  setSelectedDvr: (id: string) => void
  availableDvrs: DVRServer[]
}

const defaultContext: DvrSelectionContextValue = {
  selectedDvr: "all",
  setSelectedDvr: () => {},
  availableDvrs: [],
}

export const DvrSelectionContext = createContext<DvrSelectionContextValue>(defaultContext)

export function useDvrSelection(): DvrSelectionContextValue {
  return useContext(DvrSelectionContext)
}

interface DvrSelectionProviderProps {
  children: React.ReactNode
  availableDvrs: DVRServer[]
}

export function DvrSelectionProvider({ children, availableDvrs }: DvrSelectionProviderProps) {
  const [selectedDvr, _setSelectedDvr] = useState<string>(() => {
    if (typeof window === "undefined") return "all"
    return localStorage.getItem(LS_KEY) || "all"
  })

  useEffect(() => {
    if (selectedDvr === "all" || availableDvrs.length === 0) return
    const exists = availableDvrs.some((d) => d.id === selectedDvr)
    if (!exists) {
      _setSelectedDvr("all")
      if (typeof window !== "undefined") {
        localStorage.setItem(LS_KEY, "all")
      }
    }
  }, [availableDvrs, selectedDvr])

  const setSelectedDvr = useCallback((id: string) => {
    _setSelectedDvr(id)
    if (typeof window !== "undefined") {
      localStorage.setItem(LS_KEY, id)
    }
  }, [])

  return (
    <DvrSelectionContext.Provider value={{ selectedDvr, setSelectedDvr, availableDvrs }}>
      {children}
    </DvrSelectionContext.Provider>
  )
}
