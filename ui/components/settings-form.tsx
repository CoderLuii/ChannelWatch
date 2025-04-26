"use client"

import { useEffect, useState } from "react"
import { useForm, Controller } from "react-hook-form"
import {
  Loader2,
  Save,
  RefreshCw,
  AlertCircle,
  Info,
  Server,
  Bell,
  HardDrive,
  Clock,
  Database,
  Tv,
  Video,
  Share2,
  Copy,
  Check,
} from "lucide-react"
import { Button } from "@/components/base/button"
import { Input } from "@/components/base/input"
import { Label } from "@/components/base/label"
import { Switch } from "@/components/base/switch"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/base/select"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/base/card"
import { Alert, AlertDescription, AlertTitle } from "@/components/base/alert"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/base/tabs"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/base/tooltip"
import { Badge } from "@/components/base/badge"
import { Separator } from "@/components/base/separator"
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem } from "@/components/base/command"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/base/popover"
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/base/dialog"
import { CheckIcon, ChevronsUpDown } from "lucide-react"
import { cn } from "@/lib/utils"
import type { AppSettings } from "@/lib/types"
import { fetchSettings, saveSettings, signalRestart } from "@/lib/api"
import { useToast } from "@/hooks/use-toast"

interface SettingsFormProps {
  onSettingsSaved?: () => void
}


const timezones = [
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
  "UTC"
];

export function SettingsForm({ onSettingsSaved }: SettingsFormProps) {
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [isRestarting, setIsRestarting] = useState(false)
  const [activeTab, setActiveTab] = useState("general")
  const [saveSuccess, setSaveSuccess] = useState(false)
  const { toast } = useToast()

  const {
    register,
    setValue,
    getValues,
    handleSubmit,
    reset,
    watch,
    control,
    formState: { errors, isDirty },
  } = useForm<AppSettings>()

  
  const [enabledProviders, setEnabledProviders] = useState({
    pushover: false,
    discord: false,
    telegram: false,
    email: false,
    slack: false,
    gotify: false,
    matrix: false,
    mqtt: false,
    custom: false,
  });

  
  const isChannelWatchingEnabled = watch("alert_channel_watching");
  const isVodWatchingEnabled = watch("alert_vod_watching");
  const isRecordingEventsEnabled = watch("alert_recording_events");

  
  useEffect(() => {
    const loadSettings = async () => {
      try {
        setIsLoading(true)
        setError(null)
        const data = await fetchSettings()

        
        
        reset(data)

        setIsLoading(false)
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load settings")
        setIsLoading(false)
        
        
      }
    }

    loadSettings()
    
  }, [reset]) 

  
  useEffect(() => {
    if (!isLoading) {
      const formValues = getValues();
      setEnabledProviders({
        pushover: !!(formValues.pushover_user_key || formValues.pushover_api_token),
        discord: !!formValues.apprise_discord,
        telegram: !!formValues.apprise_telegram,
        email: !!(formValues.apprise_email || formValues.apprise_email_to),
        slack: !!formValues.apprise_slack,
        gotify: !!formValues.apprise_gotify,
        matrix: !!formValues.apprise_matrix,
        mqtt: !!formValues.apprise_mqtt,
        custom: !!formValues.apprise_custom,
      });
    }
  }, [isLoading, getValues]);

  const onSubmit = async (data: AppSettings) => {
    
    const submittedData = { ...data }; 
    
    try {
      setIsSubmitting(true)
      setError(null)
      await saveSettings(submittedData) 
      
      
      const refreshedData = await fetchSettings();
      
      reset(refreshedData, {
           keepValues: false, 
           keepDirty: false,  
           keepIsSubmitted: false, 
           
           
      });
      
      
      setSaveSuccess(true)
      
      setTimeout(() => setSaveSuccess(false), 10000)
      
      if (onSettingsSaved) {
        onSettingsSaved()
      }
    } catch (err) {
      
      setError(err instanceof Error ? err.message : "Failed to save settings")
      toast({
        variant: "destructive",
        title: "Error saving settings",
        description: err instanceof Error ? err.message : "An unknown error occurred",
      })
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleRestart = async () => {
    if (window.confirm("Are you sure you want to restart the core ChannelWatch application?")) {
      try {
        setIsRestarting(true)
        const result = await signalRestart()
        toast({
          title: "Restart signal sent",
          description: result.message,
        })
      } catch (err) {
        toast({
          variant: "destructive",
          title: "Error restarting application",
          description: err instanceof Error ? err.message : "An unknown error occurred",
        })
      } finally {
        setIsRestarting(false)
      }
    }
  }

  
  
  const renderSwitch = (fieldName: keyof AppSettings, label: string) => (
    <Controller
      control={control}
      name={fieldName}
      render={({ field }) => (
        <div className="flex items-center justify-between py-3">
          <Label htmlFor={fieldName} className="mb-0 flex-1 mr-4">
            {label}
          </Label>
          <Switch
            id={fieldName}
            checked={field.value === true} 
            onCheckedChange={(checked) => {
              field.onChange(checked);
              setValue(fieldName, checked, { shouldDirty: true });
            }}
            onBlur={field.onBlur}
            ref={field.ref}
          />
        </div>
      )}
    />
  )

  
  const renderSuccessDialog = () => {
    return null; 
  }

  if (isLoading) {
    return (
      <div className="flex justify-center items-center p-8">
        <div className="flex flex-col items-center gap-2">
          <Loader2 className="h-8 w-8 animate-spin text-primary" />
          <p className="text-sm text-muted-foreground">Loading settings...</p>
        </div>
      </div>
    )
  }

  return (
    <div className="container max-w-5xl mx-auto">
      {/* Success message banner that slides from the top */}
      {saveSuccess && (
        <div className="fixed top-16 left-0 right-0 z-10 animate-slide-from-top">
          <div className="bg-gradient-to-r from-blue-600 to-indigo-700 shadow-lg shadow-blue-900/20">
            <div className="w-full flex justify-center">
              <div className="py-2 px-8 relative flex items-center w-full justify-center">
                <div className="flex items-center gap-2.5">
                  <div className="bg-blue-500/30 backdrop-blur-sm rounded-full p-1">
                    <Check className="h-3.5 w-3.5 text-blue-100" />
                  </div>
                  <span className="font-medium text-blue-50 text-sm">Settings saved. Please restart container.</span>
                </div>
                <button 
                  onClick={() => setSaveSuccess(false)}
                  className="absolute right-4 text-blue-200/70 hover:text-blue-100 transition-colors"
                  aria-label="Dismiss"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <line x1="18" y1="6" x2="6" y2="18"></line>
                    <line x1="6" y1="6" x2="18" y2="18"></line>
                  </svg>
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
      
      <div className="flex flex-col gap-6">
        <div className="flex flex-col gap-2 relative">
          <h1 className="text-3xl font-bold tracking-tight">Settings</h1>
          <p className="text-muted-foreground">Configure ChannelWatch to match your preferences and setup.</p>
          
          {error && (
            <Alert variant="destructive">
              <AlertCircle className="h-4 w-4" />
              <AlertTitle>Error</AlertTitle>
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
        </div>

        <form onSubmit={handleSubmit(onSubmit)}>
          <div className="flex flex-col gap-8 pb-24">
            <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
              <div className="border-b">
                <div className="flex overflow-x-auto">
                  <TabsList className="inline-flex h-10 items-center justify-center rounded-none bg-transparent p-0">
                    <TabsTrigger
                      value="general"
                      className="inline-flex items-center justify-center whitespace-nowrap rounded-none border-b-2 border-b-transparent px-4 py-2 text-sm font-medium ring-offset-background transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 data-[state=active]:border-b-primary data-[state=active]:text-foreground data-[state=active]:shadow-none"
                    >
                      <Server className="mr-2 h-4 w-4" />
                      General
                    </TabsTrigger>
                    <TabsTrigger
                      value="alerts"
                      className="inline-flex items-center justify-center whitespace-nowrap rounded-none border-b-2 border-b-transparent px-4 py-2 text-sm font-medium ring-offset-background transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 data-[state=active]:border-b-primary data-[state=active]:text-foreground data-[state=active]:shadow-none"
                    >
                      <Bell className="mr-2 h-4 w-4" />
                      Alerts
                    </TabsTrigger>
                    <TabsTrigger
                      value="advanced"
                      className="inline-flex items-center justify-center whitespace-nowrap rounded-none border-b-2 border-b-transparent px-4 py-2 text-sm font-medium ring-offset-background transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 data-[state=active]:border-b-primary data-[state=active]:text-foreground data-[state=active]:shadow-none"
                    >
                      <Database className="mr-2 h-4 w-4" />
                      Advanced
                    </TabsTrigger>
                    <TabsTrigger
                      value="notifications"
                      className="inline-flex items-center justify-center whitespace-nowrap rounded-none border-b-2 border-b-transparent px-4 py-2 text-sm font-medium ring-offset-background transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 data-[state=active]:border-b-primary data-[state=active]:text-foreground data-[state=active]:shadow-none"
                    >
                      <Bell className="mr-2 h-4 w-4" />
                      Notification Services
                    </TabsTrigger>
                  </TabsList>
                </div>
              </div>

              <div className="mt-6">
                <TabsContent value="general" className="space-y-6">
                  <Card className="overflow-hidden border-blue-400/20 dark:border-blue-500/20 shadow-lg dark:shadow-blue-900/10">
                    <div className="relative">
                      <div className="absolute inset-0 bg-gradient-to-r from-blue-900/10 to-indigo-900/10 z-0"></div>
                      <div className="absolute -top-14 -right-14 w-32 h-32 rounded-full bg-blue-500/10 backdrop-blur-3xl"></div>
                      <CardHeader className="relative z-10 border-b border-blue-200/10">
                        <div className="flex items-center gap-2">
                          <div className="w-10 h-10 rounded-full bg-blue-500/20 backdrop-blur-sm flex items-center justify-center">
                            <Server className="h-5 w-5 text-blue-400" />
                          </div>
                          <div>
                            <CardTitle>Core Settings</CardTitle>
                            <CardDescription>Configure the connection to your Channels DVR server</CardDescription>
                          </div>
                        </div>
                      </CardHeader>
                    </div>
                    <CardContent className="space-y-6 relative z-10 pt-6">
                      <div className="grid gap-6 md:grid-cols-2">
                        <div className="space-y-2">
                          <Label htmlFor="channels_dvr_host" className="flex items-center gap-1">
                            DVR Host
                            <Dialog>
                              <DialogTrigger asChild>
                                <Button variant="ghost" size="icon" className="h-6 w-6 rounded-full p-0 ml-1">
                                  <Info className="h-3.5 w-3.5 text-muted-foreground" />
                                </Button>
                              </DialogTrigger>
                              <DialogContent className="max-w-lg bg-slate-950 border-slate-800">
                                <DialogHeader>
                                  <DialogTitle className="text-xl font-semibold text-blue-400">Connecting in Docker Bridge Mode</DialogTitle>
                                </DialogHeader>
                                
                                <div className="mt-4 space-y-6">
                                  <div className="border-l-4 border-blue-500 pl-4 py-2">
                                    <p className="text-sm text-slate-300">
                                      When your ChannelWatch tool and Channels DVR Server are both running in bridge mode on the same Docker host, 
                                      you need the DVR container's internal IP address to establish a connection.
                                    </p>
                                    
                                    <p className="text-sm text-slate-400 mt-2">
                                      This is necessary because in bridge mode, containers have their own isolated network namespace 
                                      with private IP addresses (typically in the 172.17.x.x range) that aren't directly accessible from outside the Docker host.
                                    </p>
                                  </div>
                                  
                                  <div className="bg-slate-900 rounded-lg overflow-hidden border border-slate-700 shadow-lg">
                                    <div className="p-4 bg-gradient-to-r from-blue-900/30 to-indigo-900/30 border-b border-slate-700">
                                      <h3 className="font-medium text-blue-300 flex items-center">
                                        <Server className="h-4 w-4 mr-2" /> Find Container IP
                                      </h3>
                                      <p className="text-xs text-slate-400 mt-1">Connect to your terminal and run one of the following commands:</p>
                                    </div>
                                    
                                    <div className="p-4 border-b border-slate-700">
                                      <h4 className="text-sm font-medium text-blue-300 mb-2">Using exact container name:</h4>
                                      <div className="bg-slate-800 p-3 rounded font-mono text-xs text-slate-300 overflow-x-auto select-all">
                                        docker inspect -f '&#123;&#123;range .NetworkSettings.Networks&#125;&#125;&#123;&#123;.IPAddress&#125;&#125;&#123;&#123;end&#125;&#125;' channels-dvr
                                      </div>
                                      <p className="text-xs mt-2 text-slate-400">Replace <span className="font-semibold text-slate-300">channels-dvr</span> with your actual Channels DVR container name.</p>
                                    </div>
                                    
                                    <div className="p-4">
                                      <h4 className="text-sm font-medium text-blue-300 mb-2">Using keyword in container name:</h4>
                                      <div className="bg-slate-800 p-3 rounded font-mono text-xs text-slate-300 overflow-x-auto select-all">
                                        docker ps --format '&#123;&#123;.Names&#125;&#125; &#123;&#123;range .NetworkSettings.Networks&#125;&#125;&#123;&#123;.IPAddress&#125;&#125;&#123;&#123;end&#125;&#125;' | grep -i channel
                                      </div>
                                      <p className="text-xs mt-2 text-slate-400">Replace <span className="font-semibold text-slate-300">channel</span> with your preferred keyword that matches your container naming pattern.</p>
                                    </div>
                                  </div>
                                  
                                  <div className="text-xs text-slate-400 mt-2 italic">
                                    The first command shows only the IP address, while the second command displays both container name and IP address for all matching containers.
                                  </div>
                                </div>
                              </DialogContent>
                            </Dialog>
                          </Label>
                          <Input
                            id="channels_dvr_host"
                            placeholder="e.g., 192.168.1.100"
                            {...register("channels_dvr_host")}
                            className="w-full"
                          />
                          <p className="text-xs text-muted-foreground">
                            IP address or hostname of your Channels DVR server
                          </p>
                        </div>

                        <div className="space-y-2">
                          <Label htmlFor="channels_dvr_port">
                            DVR Port
                          </Label>
                          <Input
                            id="channels_dvr_port"
                            type="number"
                            {...register("channels_dvr_port", { valueAsNumber: true })}
                            className="w-full"
                          />
                          <p className="text-xs text-muted-foreground">
                            Network port used to connect to your Channels DVR server (default: 8089)
                          </p>
                        </div>
                      </div>

                      <Separator />

                      <div className="grid gap-6 md:grid-cols-2">
                        <div className="space-y-2">
                          <Label htmlFor="tz">
                            Timezone
                          </Label>
                          <Controller
                            control={control}
                            name="tz"
                            render={({ field }) => (
                              <Popover>
                                <PopoverTrigger asChild>
                                  <Button
                                    variant="outline"
                                    role="combobox"
                                    className={cn(
                                      "w-full justify-between",
                                      !field.value && "text-muted-foreground"
                                    )}
                                  >
                                    {field.value ? field.value : "Select timezone..."}
                                    <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
                                  </Button>
                                </PopoverTrigger>
                                <PopoverContent className="w-[300px] p-0">
                                  <Command>
                                    <CommandInput placeholder="Search timezone..." className="h-9" />
                                    <CommandEmpty>No timezone found.</CommandEmpty>
                                    <CommandGroup className="max-h-[300px] overflow-y-auto">
                                      {timezones.map((timezone) => (
                                        <CommandItem
                                          key={timezone}
                                          value={timezone}
                                          onSelect={() => {
                                            field.onChange(timezone);
                                            setValue("tz", timezone, { shouldDirty: true });
                                          }}
                                        >
                                          {timezone}
                                          <CheckIcon
                                            className={cn(
                                              "ml-auto h-4 w-4",
                                              field.value === timezone ? "opacity-100" : "opacity-0"
                                            )}
                                          />
                                        </CommandItem>
                                      ))}
                                    </CommandGroup>
                                  </Command>
                                </PopoverContent>
                              </Popover>
                            )}
                          />
                          <p className="text-xs text-muted-foreground">
                            Set the timezone for accurate timestamps in logs and notifications
                          </p>
                        </div>

                        <div className="space-y-2">
                          <Label htmlFor="log_level">Log Level</Label>
                          <Select
                            onValueChange={(value) => setValue("log_level", Number.parseInt(value), { shouldDirty: true })}
                            value={watch("log_level")?.toString()}
                          >
                            <SelectTrigger>
                              <SelectValue placeholder="Select log level" />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="1">Standard</SelectItem>
                              <SelectItem value="2">Verbose</SelectItem>
                            </SelectContent>
                          </Select>
                          <p className="text-xs text-muted-foreground">
                            Control the detail level in application logs
                          </p>
                        </div>
                      </div>

                      <div className="space-y-2">
                        <Label htmlFor="log_retention_days">Log Retention (Days)</Label>
                        <Input
                          id="log_retention_days"
                          type="number"
                          min="1"
                          {...register("log_retention_days", { valueAsNumber: true })}
                          className="max-w-xs"
                        />
                        <p className="text-xs text-muted-foreground">
                          Automatically delete logs older than specified number of days
                        </p>
                      </div>
                    </CardContent>
                  </Card>
                </TabsContent>

                <TabsContent value="alerts" className="space-y-6">
                  <Card className="overflow-hidden border-blue-400/20 dark:border-blue-500/20 shadow-lg dark:shadow-blue-900/10">
                    <div className="relative">
                      <div className="absolute inset-0 bg-gradient-to-r from-blue-900/10 to-indigo-900/10 z-0"></div>
                      <div className="absolute -top-14 -right-14 w-32 h-32 rounded-full bg-blue-500/10 backdrop-blur-3xl"></div>
                      <CardHeader className="relative z-10 border-b border-blue-200/10">
                        <div className="flex items-center gap-2">
                          <div className="w-10 h-10 rounded-full bg-blue-500/20 backdrop-blur-sm flex items-center justify-center">
                            <Bell className="h-5 w-5 text-blue-400" />
                          </div>
                          <div>
                            <CardTitle>Alert Modules</CardTitle>
                            <CardDescription>Enable or disable different types of alerts</CardDescription>
                          </div>
                        </div>
                      </CardHeader>
                    </div>
                    <CardContent className="space-y-6 relative z-10 pt-6">
                      {/* Changed from a two-column grid to a single column for main alert modules */}
                      <div className="space-y-4">
                        <div className="flex flex-row items-center justify-between p-4 rounded-xl border border-blue-400/20 bg-blue-500/5 backdrop-blur-sm shadow-sm transition-colors hover:bg-blue-500/10">
                          <div className="flex gap-3">
                            <div className="flex-shrink-0 w-10 h-10 rounded-full bg-blue-500/20 flex items-center justify-center">
                              <Tv className="h-5 w-5 text-blue-400" />
                            </div>
                            <div className="space-y-0.5">
                              <Label htmlFor="alert_channel_watching" className="text-base font-medium">
                                Channel Watching
                              </Label>
                              <p className="text-sm text-muted-foreground">
                                Receive notifications when live TV channels are being watched
                              </p>
                            </div>
                          </div>
                          <Switch
                            id="alert_channel_watching"
                            checked={watch("alert_channel_watching")}
                            onCheckedChange={(checked) => {
                              setValue("alert_channel_watching", checked, { shouldDirty: true });
                            }}
                            className="data-[state=checked]:bg-blue-600"
                          />
                        </div>

                        <div className="flex flex-row items-center justify-between p-4 rounded-xl border border-blue-400/20 bg-blue-500/5 backdrop-blur-sm shadow-sm transition-colors hover:bg-blue-500/10">
                          <div className="flex gap-3">
                            <div className="flex-shrink-0 w-10 h-10 rounded-full bg-blue-500/20 flex items-center justify-center">
                              <Video className="h-5 w-5 text-blue-400" />
                            </div>
                            <div className="space-y-0.5">
                              <Label htmlFor="alert_vod_watching" className="text-base font-medium">
                                VOD Watching
                              </Label>
                              <p className="text-sm text-muted-foreground">
                                Get notified when recorded content or movies are played
                              </p>
                            </div>
                          </div>
                          <Switch
                            id="alert_vod_watching"
                            checked={watch("alert_vod_watching")}
                            onCheckedChange={(checked) => {
                              setValue("alert_vod_watching", checked, { shouldDirty: true });
                            }}
                            className="data-[state=checked]:bg-blue-600"
                          />
                        </div>

                        <div className="flex flex-row items-center justify-between p-4 rounded-xl border border-blue-400/20 bg-blue-500/5 backdrop-blur-sm shadow-sm transition-colors hover:bg-blue-500/10">
                          <div className="flex gap-3">
                            <div className="flex-shrink-0 w-10 h-10 rounded-full bg-blue-500/20 flex items-center justify-center">
                              <Clock className="h-5 w-5 text-blue-400" />
                            </div>
                            <div className="space-y-0.5">
                              <Label htmlFor="alert_recording_events" className="text-base font-medium">
                                Recording Events
                              </Label>
                              <p className="text-sm text-muted-foreground">
                                Track recording activity from scheduling through completion
                              </p>
                            </div>
                          </div>
                          <Switch
                            id="alert_recording_events"
                            checked={watch("alert_recording_events")}
                            onCheckedChange={(checked) => {
                              setValue("alert_recording_events", checked, { shouldDirty: true });
                            }}
                            className="data-[state=checked]:bg-blue-600"
                          />
                        </div>
                      </div>

                      <div className="relative flex items-center space-x-2 my-6">
                        <div className="h-px flex-1 bg-gradient-to-r from-transparent to-blue-400/30"></div>
                        <div className="text-blue-400 font-medium text-sm px-3 py-1 rounded-full bg-blue-500/10 backdrop-blur-sm border border-blue-500/20">Global Alert Options</div>
                        <div className="h-px flex-1 bg-gradient-to-l from-transparent to-blue-400/30"></div>
                      </div>

                      <div className="flex flex-row items-center justify-between p-4 rounded-xl border border-blue-400/20 bg-blue-600/5 backdrop-blur-sm shadow-sm relative overflow-hidden">
                        <div className="absolute -top-12 -right-12 w-24 h-24 rounded-full bg-blue-600/10 backdrop-blur-xl"></div>
                        <div className="absolute -bottom-8 -left-8 w-16 h-16 rounded-full bg-indigo-600/10 backdrop-blur-xl"></div>
                        <div className="flex gap-3 relative z-10">
                          <div className="flex-shrink-0 w-10 h-10 rounded-full bg-blue-500/20 flex items-center justify-center">
                            <Tv className="h-5 w-5 text-blue-400" />
                          </div>
                          <div className="space-y-0.5">
                            <Label htmlFor="stream_count" className="text-base font-medium">
                              Count all streams together
                            </Label>
                            <p className="text-sm text-muted-foreground">
                              Add a "Total Streams" counter to channel and recording notifications
                              <span className="block mt-1 text-xs italic">
                                (Applies to Channel Watching and Recording Events only)
                              </span>
                            </p>
                          </div>
                        </div>
                        <Switch
                          id="stream_count"
                          checked={watch("stream_count")}
                          onCheckedChange={(checked) => {
                            setValue("stream_count", checked, { shouldDirty: true });
                          }}
                          className="data-[state=checked]:bg-blue-600 relative z-10"
                        />
                      </div>
                    </CardContent>
                  </Card>

                  <div className="grid gap-6 md:grid-cols-3">
                    {/* Channel Watching Alerts Card */}
                    <div className={cn(
                      "transition-opacity duration-300",
                      !isChannelWatchingEnabled && "opacity-50 pointer-events-none"
                    )}>
                      <Card className="overflow-hidden border-blue-400/20 dark:border-blue-500/20 shadow-lg dark:shadow-blue-900/10 md:min-h-[680px]">
                        <div className="relative">
                          <div className="absolute inset-0 bg-gradient-to-r from-blue-900/10 to-indigo-900/10 z-0"></div>
                          <div className="absolute -bottom-14 -right-14 w-32 h-32 rounded-full bg-blue-500/10 backdrop-blur-3xl"></div>
                          <CardHeader className="relative z-10 border-b border-blue-200/10">
                            <div className="flex items-center gap-2">
                              <div className="w-10 h-10 rounded-full bg-blue-500/20 backdrop-blur-sm flex items-center justify-center">
                                <Tv className="h-5 w-5 text-blue-400" />
                              </div>
                              <div>
                                <CardTitle>Channel Watching Alerts</CardTitle>
                                <CardDescription>Configure what information to include</CardDescription>
                              </div>
                            </div>
                          </CardHeader>
                        </div>
                        <CardContent className="space-y-6 relative z-10 pt-6">
                          <div className="space-y-3">
                            <div className="flex items-center gap-2">
                              <div className="w-1.5 h-1.5 rounded-full bg-blue-400"></div>
                              <Label htmlFor="cw_image_source" className="font-medium text-blue-300">Image Source</Label>
                            </div>
                            <Select
                              onValueChange={(value) => setValue("cw_image_source", value, { shouldDirty: true })}
                              value={watch("cw_image_source")}
                              disabled={!isChannelWatchingEnabled}
                            >
                              <SelectTrigger className="border-blue-400/20 bg-blue-500/5 backdrop-blur-sm">
                                <SelectValue placeholder="Select image source" />
                              </SelectTrigger>
                              <SelectContent>
                                <SelectItem value="PROGRAM">Program Image</SelectItem>
                                <SelectItem value="CHANNEL">Channel Logo</SelectItem>
                              </SelectContent>
                            </Select>
                          </div>

                          <div className="relative flex items-center space-x-2 my-6">
                            <div className="h-px flex-1 bg-gradient-to-r from-transparent to-blue-400/30"></div>
                            <div className="text-blue-400 font-medium text-sm px-3 py-1 rounded-full bg-blue-500/10 backdrop-blur-sm border border-blue-500/20">Details to Include</div>
                            <div className="h-px flex-1 bg-gradient-to-l from-transparent to-blue-400/30"></div>
                          </div>

                          <div className="grid grid-cols-1 gap-3 bg-blue-500/5 rounded-xl p-4 border border-blue-400/10 shadow-sm backdrop-blur-sm">
                            <div className="flex items-center justify-between p-2 rounded-lg transition-colors hover:bg-blue-500/10">
                              <Label htmlFor="cw_channel_name" className="flex items-center gap-3 cursor-pointer w-[70%]">
                                <div className="w-4 h-4 rounded-full bg-blue-500/20 flex items-center justify-center flex-shrink-0">
                                  <div className="w-1 h-1 rounded-full bg-blue-400"></div>
                                </div>
                                <span className="whitespace-nowrap">Channel Name</span>
                              </Label>
                              <Switch
                                id="cw_channel_name"
                                checked={watch("cw_channel_name")}
                                onCheckedChange={(checked) => setValue("cw_channel_name", checked, { shouldDirty: true })}
                                className="data-[state=checked]:bg-blue-600"
                                disabled={!isChannelWatchingEnabled}
                              />
                            </div>

                            <div className="flex items-center justify-between p-2 rounded-lg transition-colors hover:bg-blue-500/10">
                              <Label htmlFor="cw_channel_number" className="flex items-center gap-3 cursor-pointer w-[70%]">
                                <div className="w-4 h-4 rounded-full bg-blue-500/20 flex items-center justify-center flex-shrink-0">
                                  <div className="w-1 h-1 rounded-full bg-blue-400"></div>
                                </div>
                                <span className="whitespace-nowrap">Channel Number</span>
                              </Label>
                              <Switch
                                id="cw_channel_number"
                                checked={watch("cw_channel_number")}
                                onCheckedChange={(checked) => setValue("cw_channel_number", checked, { shouldDirty: true })}
                                className="data-[state=checked]:bg-blue-600"
                                disabled={!isChannelWatchingEnabled}
                              />
                            </div>

                            <div className="flex items-center justify-between p-2 rounded-lg transition-colors hover:bg-blue-500/10">
                              <Label htmlFor="cw_program_name" className="flex items-center gap-3 cursor-pointer w-[70%]">
                                <div className="w-4 h-4 rounded-full bg-blue-500/20 flex items-center justify-center flex-shrink-0">
                                  <div className="w-1 h-1 rounded-full bg-blue-400"></div>
                                </div>
                                <span className="whitespace-nowrap">Program Name</span>
                              </Label>
                              <Switch
                                id="cw_program_name"
                                checked={watch("cw_program_name")}
                                onCheckedChange={(checked) => setValue("cw_program_name", checked, { shouldDirty: true })}
                                className="data-[state=checked]:bg-blue-600"
                                disabled={!isChannelWatchingEnabled}
                              />
                            </div>

                            <div className="flex items-center justify-between p-2 rounded-lg transition-colors hover:bg-blue-500/10">
                              <Label htmlFor="cw_device_name" className="flex items-center gap-3 cursor-pointer w-[70%]">
                                <div className="w-4 h-4 rounded-full bg-blue-500/20 flex items-center justify-center flex-shrink-0">
                                  <div className="w-1 h-1 rounded-full bg-blue-400"></div>
                                </div>
                                <span className="whitespace-nowrap">Device Name</span>
                              </Label>
                              <Switch
                                id="cw_device_name"
                                checked={watch("cw_device_name")}
                                onCheckedChange={(checked) => setValue("cw_device_name", checked, { shouldDirty: true })}
                                className="data-[state=checked]:bg-blue-600"
                                disabled={!isChannelWatchingEnabled}
                              />
                            </div>

                            <div className="flex items-center justify-between p-2 rounded-lg transition-colors hover:bg-blue-500/10">
                              <Label htmlFor="cw_device_ip" className="flex items-center gap-3 cursor-pointer w-[70%]">
                                <div className="w-4 h-4 rounded-full bg-blue-500/20 flex items-center justify-center flex-shrink-0">
                                  <div className="w-1 h-1 rounded-full bg-blue-400"></div>
                                </div>
                                <span className="whitespace-nowrap">Device IP</span>
                              </Label>
                              <Switch
                                id="cw_device_ip"
                                checked={watch("cw_device_ip")}
                                onCheckedChange={(checked) => setValue("cw_device_ip", checked, { shouldDirty: true })}
                                className="data-[state=checked]:bg-blue-600"
                                disabled={!isChannelWatchingEnabled}
                              />
                            </div>

                            <div className="flex items-center justify-between p-2 rounded-lg transition-colors hover:bg-blue-500/10">
                              <Label htmlFor="cw_stream_source" className="flex items-center gap-3 cursor-pointer w-[70%]">
                                <div className="w-4 h-4 rounded-full bg-blue-500/20 flex items-center justify-center flex-shrink-0">
                                  <div className="w-1 h-1 rounded-full bg-blue-400"></div>
                                </div>
                                <span className="whitespace-nowrap">Stream Source</span>
                              </Label>
                              <Switch
                                id="cw_stream_source"
                                checked={watch("cw_stream_source")}
                                onCheckedChange={(checked) => setValue("cw_stream_source", checked, { shouldDirty: true })}
                                className="data-[state=checked]:bg-blue-600"
                                disabled={!isChannelWatchingEnabled}
                              />
                            </div>
                          </div>
                        </CardContent>
                      </Card>
                    </div>

                    {/* VOD Watching Alerts Card */}
                    <div className={cn(
                      "transition-opacity duration-300",
                      !isVodWatchingEnabled && "opacity-50 pointer-events-none"
                    )}>
                      <Card className="overflow-hidden border-blue-400/20 dark:border-blue-500/20 shadow-lg dark:shadow-blue-900/10 md:min-h-[680px]">
                        <div className="relative">
                          <div className="absolute inset-0 bg-gradient-to-r from-blue-900/10 to-indigo-900/10 z-0"></div>
                          <div className="absolute -bottom-14 -left-14 w-32 h-32 rounded-full bg-blue-500/10 backdrop-blur-3xl"></div>
                          <CardHeader className="relative z-10 border-b border-blue-200/10">
                            <div className="flex items-center gap-2">
                              <div className="w-10 h-10 rounded-full bg-blue-500/20 backdrop-blur-sm flex items-center justify-center">
                                <Video className="h-5 w-5 text-blue-400" />
                              </div>
                              <div>
                                <CardTitle>VOD Watching Alerts</CardTitle>
                                <CardDescription>Configure recorded content alerts</CardDescription>
                              </div>
                            </div>
                          </CardHeader>
                        </div>
                        <CardContent className="space-y-6 relative z-10 pt-6">
                          <div className="space-y-3">
                            <div className="flex items-center gap-2">
                              <div className="w-1.5 h-1.5 rounded-full bg-blue-400"></div>
                              <h3 className="font-medium text-blue-300">Content Settings</h3>
                            </div>
                            <div className="grid grid-cols-1 gap-1 bg-blue-500/5 rounded-xl p-3 border border-blue-400/10 shadow-sm backdrop-blur-sm">
                              <div className="flex items-center justify-between p-2 rounded-lg transition-colors hover:bg-blue-500/10">
                                <Label htmlFor="vod_title" className="flex items-center gap-3 cursor-pointer w-[70%]">
                                  <div className="w-4 h-4 rounded-full bg-blue-500/20 flex items-center justify-center flex-shrink-0">
                                    <div className="w-1 h-1 rounded-full bg-blue-400"></div>
                                  </div>
                                  <span className="whitespace-nowrap">Title</span>
                                </Label>
                                <Switch
                                  id="vod_title"
                                  checked={watch("vod_title")}
                                  onCheckedChange={(checked) => setValue("vod_title", checked, { shouldDirty: true })}
                                  className="data-[state=checked]:bg-blue-600"
                                  disabled={!isVodWatchingEnabled}
                                />
                              </div>

                              <div className="flex items-center justify-between p-2 rounded-lg transition-colors hover:bg-blue-500/10">
                                <Label htmlFor="vod_episode_title" className="flex items-center gap-3 cursor-pointer w-[70%]">
                                  <div className="w-4 h-4 rounded-full bg-blue-500/20 flex items-center justify-center flex-shrink-0">
                                    <div className="w-1 h-1 rounded-full bg-blue-400"></div>
                                  </div>
                                  <span className="whitespace-nowrap">Episode Title</span>
                                </Label>
                                <Switch
                                  id="vod_episode_title"
                                  checked={watch("vod_episode_title")}
                                  onCheckedChange={(checked) => setValue("vod_episode_title", checked, { shouldDirty: true })}
                                  className="data-[state=checked]:bg-blue-600"
                                  disabled={!isVodWatchingEnabled}
                                />
                              </div>

                              <div className="flex items-center justify-between p-2 rounded-lg transition-colors hover:bg-blue-500/10">
                                <Label htmlFor="vod_summary" className="flex items-center gap-3 cursor-pointer w-[70%]">
                                  <div className="w-4 h-4 rounded-full bg-blue-500/20 flex items-center justify-center flex-shrink-0">
                                    <div className="w-1 h-1 rounded-full bg-blue-400"></div>
                                  </div>
                                  <span className="whitespace-nowrap">Summary</span>
                                </Label>
                                <Switch
                                  id="vod_summary"
                                  checked={watch("vod_summary")}
                                  onCheckedChange={(checked) => setValue("vod_summary", checked, { shouldDirty: true })}
                                  className="data-[state=checked]:bg-blue-600"
                                  disabled={!isVodWatchingEnabled}
                                />
                              </div>

                              <div className="flex items-center justify-between p-2 rounded-lg transition-colors hover:bg-blue-500/10">
                                <Label htmlFor="vod_image" className="flex items-center gap-3 cursor-pointer w-[70%]">
                                  <div className="w-4 h-4 rounded-full bg-blue-500/20 flex items-center justify-center flex-shrink-0">
                                    <div className="w-1 h-1 rounded-full bg-blue-400"></div>
                                  </div>
                                  <span className="whitespace-nowrap">Content Image</span>
                                </Label>
                                <Switch
                                  id="vod_image"
                                  checked={watch("vod_image")}
                                  onCheckedChange={(checked) => setValue("vod_image", checked, { shouldDirty: true })}
                                  className="data-[state=checked]:bg-blue-600"
                                  disabled={!isVodWatchingEnabled}
                                />
                              </div>
                            </div>
                          </div>

                          <div className="relative flex items-center space-x-2 my-4">
                            <div className="h-px flex-1 bg-gradient-to-r from-transparent to-blue-400/30"></div>
                            <div className="text-blue-400 font-medium text-sm px-3 py-1 rounded-full bg-blue-500/10 backdrop-blur-sm border border-blue-500/20">Playback Details</div>
                            <div className="h-px flex-1 bg-gradient-to-l from-transparent to-blue-400/30"></div>
                          </div>

                          <div className="grid grid-cols-1 gap-1 bg-blue-500/5 rounded-xl p-3 border border-blue-400/10 shadow-sm backdrop-blur-sm">
                            <div className="flex items-center justify-between p-2 rounded-lg transition-colors hover:bg-blue-500/10">
                              <Label htmlFor="vod_duration" className="flex items-center gap-3 cursor-pointer w-[70%]">
                                <div className="w-4 h-4 rounded-full bg-blue-500/20 flex items-center justify-center flex-shrink-0">
                                  <div className="w-1 h-1 rounded-full bg-blue-400"></div>
                                </div>
                                <span className="whitespace-nowrap">Duration</span>
                              </Label>
                              <Switch
                                id="vod_duration"
                                checked={watch("vod_duration")}
                                onCheckedChange={(checked) => setValue("vod_duration", checked, { shouldDirty: true })}
                                className="data-[state=checked]:bg-blue-600"
                                disabled={!isVodWatchingEnabled}
                              />
                            </div>

                            <div className="flex items-center justify-between p-2 rounded-lg transition-colors hover:bg-blue-500/10">
                              <Label htmlFor="vod_progress" className="flex items-center gap-3 cursor-pointer w-[70%]">
                                <div className="w-4 h-4 rounded-full bg-blue-500/20 flex items-center justify-center flex-shrink-0">
                                  <div className="w-1 h-1 rounded-full bg-blue-400"></div>
                                </div>
                                <span className="whitespace-nowrap">Progress</span>
                              </Label>
                              <Switch
                                id="vod_progress"
                                checked={watch("vod_progress")}
                                onCheckedChange={(checked) => setValue("vod_progress", checked, { shouldDirty: true })}
                                className="data-[state=checked]:bg-blue-600"
                                disabled={!isVodWatchingEnabled}
                              />
                            </div>

                            <div className="flex items-center justify-between p-2 rounded-lg transition-colors hover:bg-blue-500/10">
                              <Label htmlFor="vod_rating" className="flex items-center gap-3 cursor-pointer w-[70%]">
                                <div className="w-4 h-4 rounded-full bg-blue-500/20 flex items-center justify-center flex-shrink-0">
                                  <div className="w-1 h-1 rounded-full bg-blue-400"></div>
                                </div>
                                <span className="whitespace-nowrap">Rating</span>
                              </Label>
                              <Switch
                                id="vod_rating"
                                checked={watch("vod_rating")}
                                onCheckedChange={(checked) => setValue("vod_rating", checked, { shouldDirty: true })}
                                className="data-[state=checked]:bg-blue-600"
                                disabled={!isVodWatchingEnabled}
                              />
                            </div>

                            <div className="flex items-center justify-between p-2 rounded-lg transition-colors hover:bg-blue-500/10">
                              <Label htmlFor="vod_genres" className="flex items-center gap-3 cursor-pointer w-[70%]">
                                <div className="w-4 h-4 rounded-full bg-blue-500/20 flex items-center justify-center flex-shrink-0">
                                  <div className="w-1 h-1 rounded-full bg-blue-400"></div>
                                </div>
                                <span className="whitespace-nowrap">Genres</span>
                              </Label>
                              <Switch
                                id="vod_genres"
                                checked={watch("vod_genres")}
                                onCheckedChange={(checked) => setValue("vod_genres", checked, { shouldDirty: true })}
                                className="data-[state=checked]:bg-blue-600"
                                disabled={!isVodWatchingEnabled}
                              />
                            </div>

                            <div className="flex items-center justify-between p-2 rounded-lg transition-colors hover:bg-blue-500/10">
                              <Label htmlFor="vod_cast" className="flex items-center gap-3 cursor-pointer w-[70%]">
                                <div className="w-4 h-4 rounded-full bg-blue-500/20 flex items-center justify-center flex-shrink-0">
                                  <div className="w-1 h-1 rounded-full bg-blue-400"></div>
                                </div>
                                <span className="whitespace-nowrap">Cast</span>
                              </Label>
                              <Switch
                                id="vod_cast"
                                checked={watch("vod_cast")}
                                onCheckedChange={(checked) => setValue("vod_cast", checked, { shouldDirty: true })}
                                className="data-[state=checked]:bg-blue-600"
                                disabled={!isVodWatchingEnabled}
                              />
                            </div>

                            <div className="flex items-center justify-between p-2 rounded-lg transition-colors hover:bg-blue-500/10">
                              <Label htmlFor="vod_device_name" className="flex items-center gap-3 cursor-pointer w-[70%]">
                                <div className="w-4 h-4 rounded-full bg-blue-500/20 flex items-center justify-center flex-shrink-0">
                                  <div className="w-1 h-1 rounded-full bg-blue-400"></div>
                                </div>
                                <span className="whitespace-nowrap">Device Name</span>
                              </Label>
                              <Switch
                                id="vod_device_name"
                                checked={watch("vod_device_name")}
                                onCheckedChange={(checked) => setValue("vod_device_name", checked, { shouldDirty: true })}
                                className="data-[state=checked]:bg-blue-600"
                                disabled={!isVodWatchingEnabled}
                              />
                            </div>

                            <div className="flex items-center justify-between p-2 rounded-lg transition-colors hover:bg-blue-500/10">
                              <Label htmlFor="vod_device_ip" className="flex items-center gap-3 cursor-pointer w-[70%]">
                                <div className="w-4 h-4 rounded-full bg-blue-500/20 flex items-center justify-center flex-shrink-0">
                                  <div className="w-1 h-1 rounded-full bg-blue-400"></div>
                                </div>
                                <span className="whitespace-nowrap">Device IP</span>
                              </Label>
                              <Switch
                                id="vod_device_ip"
                                checked={watch("vod_device_ip")}
                                onCheckedChange={(checked) => setValue("vod_device_ip", checked, { shouldDirty: true })}
                                className="data-[state=checked]:bg-blue-600"
                                disabled={!isVodWatchingEnabled}
                              />
                            </div>
                          </div>
                        </CardContent>
                      </Card>
                    </div>

                    {/* Recording Events Alerts Card */}
                    <div className={cn(
                      "transition-opacity duration-300",
                      !isRecordingEventsEnabled && "opacity-50 pointer-events-none"
                    )}>
                      <Card className="overflow-hidden border-blue-400/20 dark:border-blue-500/20 shadow-lg dark:shadow-blue-900/10 md:min-h-[680px]">
                        <div className="relative">
                          <div className="absolute inset-0 bg-gradient-to-r from-blue-900/10 to-indigo-900/10 z-0"></div>
                          <div className="absolute -bottom-14 -left-14 w-32 h-32 rounded-full bg-blue-500/10 backdrop-blur-3xl"></div>
                          <CardHeader className="relative z-10 border-b border-blue-200/10">
                            <div className="flex items-center gap-2">
                              <div className="w-10 h-10 rounded-full bg-blue-500/20 backdrop-blur-sm flex items-center justify-center">
                                <Video className="h-5 w-5 text-blue-400" />
                              </div>
                              <div>
                                <CardTitle>Recording Events Alerts</CardTitle>
                                <CardDescription>Configure recording notifications</CardDescription>
                              </div>
                            </div>
                          </CardHeader>
                        </div>
                        <CardContent className="space-y-4 relative z-10 pt-6">
                          <div className="space-y-3">
                            <div className="flex items-center gap-2">
                              <div className="w-1.5 h-1.5 rounded-full bg-blue-400"></div>
                              <h3 className="font-medium text-blue-300">Event Types to Monitor</h3>
                            </div>
                            <div className="grid grid-cols-1 gap-1 bg-blue-500/5 rounded-xl p-3 border border-blue-400/10 shadow-sm backdrop-blur-sm">
                              <div className="flex items-center justify-between p-2 rounded-lg transition-colors hover:bg-blue-500/10">
                                <Label htmlFor="rd_alert_scheduled" className="flex items-center gap-3 cursor-pointer w-[70%]">
                                  <div className="w-4 h-4 rounded-full bg-blue-500/20 flex items-center justify-center flex-shrink-0">
                                    <div className="w-1 h-1 rounded-full bg-blue-400"></div>
                                  </div>
                                  <span className="whitespace-nowrap">Scheduled</span>
                                </Label>
                                <Switch
                                  id="rd_alert_scheduled"
                                  checked={watch("rd_alert_scheduled")}
                                  onCheckedChange={(checked) => setValue("rd_alert_scheduled", checked, { shouldDirty: true })}
                                  className="data-[state=checked]:bg-blue-600"
                                  disabled={!isRecordingEventsEnabled}
                                />
                              </div>

                              <div className="flex items-center justify-between p-2 rounded-lg transition-colors hover:bg-blue-500/10">
                                <Label htmlFor="rd_alert_started" className="flex items-center gap-3 cursor-pointer w-[70%]">
                                  <div className="w-4 h-4 rounded-full bg-blue-500/20 flex items-center justify-center flex-shrink-0">
                                    <div className="w-1 h-1 rounded-full bg-blue-400"></div>
                                  </div>
                                  <span className="whitespace-nowrap">Started</span>
                                </Label>
                                <Switch
                                  id="rd_alert_started"
                                  checked={watch("rd_alert_started")}
                                  onCheckedChange={(checked) => setValue("rd_alert_started", checked, { shouldDirty: true })}
                                  className="data-[state=checked]:bg-blue-600"
                                  disabled={!isRecordingEventsEnabled}
                                />
                              </div>

                              <div className="flex items-center justify-between p-2 rounded-lg transition-colors hover:bg-blue-500/10">
                                <Label htmlFor="rd_alert_completed" className="flex items-center gap-3 cursor-pointer w-[70%]">
                                  <div className="w-4 h-4 rounded-full bg-blue-500/20 flex items-center justify-center flex-shrink-0">
                                    <div className="w-1 h-1 rounded-full bg-blue-400"></div>
                                  </div>
                                  <span className="whitespace-nowrap">Completed</span>
                                </Label>
                                <Switch
                                  id="rd_alert_completed"
                                  checked={watch("rd_alert_completed")}
                                  onCheckedChange={(checked) => setValue("rd_alert_completed", checked, { shouldDirty: true })}
                                  className="data-[state=checked]:bg-blue-600"
                                  disabled={!isRecordingEventsEnabled}
                                />
                              </div>

                              <div className="flex items-center justify-between p-2 rounded-lg transition-colors hover:bg-blue-500/10">
                                <Label htmlFor="rd_alert_cancelled" className="flex items-center gap-3 cursor-pointer w-[70%]">
                                  <div className="w-4 h-4 rounded-full bg-blue-500/20 flex items-center justify-center flex-shrink-0">
                                    <div className="w-1 h-1 rounded-full bg-blue-400"></div>
                                  </div>
                                  <span className="whitespace-nowrap">Cancelled</span>
                                </Label>
                                <Switch
                                  id="rd_alert_cancelled"
                                  checked={watch("rd_alert_cancelled")}
                                  onCheckedChange={(checked) => setValue("rd_alert_cancelled", checked, { shouldDirty: true })}
                                  className="data-[state=checked]:bg-blue-600"
                                  disabled={!isRecordingEventsEnabled}
                                />
                              </div>
                            </div>
                          </div>

                          <div className="relative flex items-center space-x-2 my-4">
                            <div className="h-px flex-1 bg-gradient-to-r from-transparent to-blue-400/30"></div>
                            <div className="text-blue-400 font-medium text-sm px-3 py-1 rounded-full bg-blue-500/10 backdrop-blur-sm border border-blue-500/20">Details to Include</div>
                            <div className="h-px flex-1 bg-gradient-to-l from-transparent to-blue-400/30"></div>
                          </div>

                          <div className="grid grid-cols-1 gap-1 bg-blue-500/5 rounded-xl p-3 border border-blue-400/10 shadow-sm backdrop-blur-sm">
                            <div className="flex items-center justify-between p-2 rounded-lg transition-colors hover:bg-blue-500/10">
                              <Label htmlFor="rd_program_name" className="flex items-center gap-3 cursor-pointer w-[70%]">
                                <div className="w-4 h-4 rounded-full bg-blue-500/20 flex items-center justify-center flex-shrink-0">
                                  <div className="w-1 h-1 rounded-full bg-blue-400"></div>
                                </div>
                                <span className="whitespace-nowrap">Program Name</span>
                              </Label>
                              <Switch
                                id="rd_program_name"
                                checked={watch("rd_program_name")}
                                onCheckedChange={(checked) => setValue("rd_program_name", checked, { shouldDirty: true })}
                                className="data-[state=checked]:bg-blue-600"
                                disabled={!isRecordingEventsEnabled}
                              />
                            </div>

                            <div className="flex items-center justify-between p-2 rounded-lg transition-colors hover:bg-blue-500/10">
                              <Label htmlFor="rd_program_desc" className="flex items-center gap-3 cursor-pointer w-[70%]">
                                <div className="w-4 h-4 rounded-full bg-blue-500/20 flex items-center justify-center flex-shrink-0">
                                  <div className="w-1 h-1 rounded-full bg-blue-400"></div>
                                </div>
                                <span className="whitespace-nowrap">Description</span>
                              </Label>
                              <Switch
                                id="rd_program_desc"
                                checked={watch("rd_program_desc")}
                                onCheckedChange={(checked) => setValue("rd_program_desc", checked, { shouldDirty: true })}
                                className="data-[state=checked]:bg-blue-600"
                                disabled={!isRecordingEventsEnabled}
                              />
                            </div>

                            <div className="flex items-center justify-between p-2 rounded-lg transition-colors hover:bg-blue-500/10">
                              <Label htmlFor="rd_duration" className="flex items-center gap-3 cursor-pointer w-[70%]">
                                <div className="w-4 h-4 rounded-full bg-blue-500/20 flex items-center justify-center flex-shrink-0">
                                  <div className="w-1 h-1 rounded-full bg-blue-400"></div>
                                </div>
                                <span className="whitespace-nowrap">Duration</span>
                              </Label>
                              <Switch
                                id="rd_duration"
                                checked={watch("rd_duration")}
                                onCheckedChange={(checked) => setValue("rd_duration", checked, { shouldDirty: true })}
                                className="data-[state=checked]:bg-blue-600"
                                disabled={!isRecordingEventsEnabled}
                              />
                            </div>

                            <div className="flex items-center justify-between p-2 rounded-lg transition-colors hover:bg-blue-500/10">
                              <Label htmlFor="rd_channel_name" className="flex items-center gap-3 cursor-pointer w-[70%]">
                                <div className="w-4 h-4 rounded-full bg-blue-500/20 flex items-center justify-center flex-shrink-0">
                                  <div className="w-1 h-1 rounded-full bg-blue-400"></div>
                                </div>
                                <span className="whitespace-nowrap">Channel Name</span>
                              </Label>
                              <Switch
                                id="rd_channel_name"
                                checked={watch("rd_channel_name")}
                                onCheckedChange={(checked) => setValue("rd_channel_name", checked, { shouldDirty: true })}
                                className="data-[state=checked]:bg-blue-600"
                                disabled={!isRecordingEventsEnabled}
                              />
                            </div>

                            <div className="flex items-center justify-between p-2 rounded-lg transition-colors hover:bg-blue-500/10">
                              <Label htmlFor="rd_channel_number" className="flex items-center gap-3 cursor-pointer w-[70%]">
                                <div className="w-4 h-4 rounded-full bg-blue-500/20 flex items-center justify-center flex-shrink-0">
                                  <div className="w-1 h-1 rounded-full bg-blue-400"></div>
                                </div>
                                <span className="whitespace-nowrap">Channel Number</span>
                              </Label>
                              <Switch
                                id="rd_channel_number"
                                checked={watch("rd_channel_number")}
                                onCheckedChange={(checked) => setValue("rd_channel_number", checked, { shouldDirty: true })}
                                className="data-[state=checked]:bg-blue-600"
                                disabled={!isRecordingEventsEnabled}
                              />
                            </div>

                            <div className="flex items-center justify-between p-2 rounded-lg transition-colors hover:bg-blue-500/10">
                              <Label htmlFor="rd_type" className="flex items-center gap-3 cursor-pointer w-[70%]">
                                <div className="w-4 h-4 rounded-full bg-blue-500/20 flex items-center justify-center flex-shrink-0">
                                  <div className="w-1 h-1 rounded-full bg-blue-400"></div>
                                </div>
                                <span className="whitespace-nowrap">Recording Type</span>
                              </Label>
                              <Switch
                                id="rd_type"
                                checked={watch("rd_type")}
                                onCheckedChange={(checked) => setValue("rd_type", checked, { shouldDirty: true })}
                                className="data-[state=checked]:bg-blue-600"
                                disabled={!isRecordingEventsEnabled}
                              />
                            </div>
                          </div>
                        </CardContent>
                      </Card>
                    </div>
                  </div>
                </TabsContent>

                <TabsContent value="advanced" className="space-y-6">
                  <div className="grid gap-6 md:grid-cols-2">
                    <Card className="overflow-hidden border-blue-400/20 dark:border-blue-500/20 shadow-lg dark:shadow-blue-900/10">
                      <div className="relative">
                        <div className="absolute inset-0 bg-gradient-to-r from-blue-900/10 to-indigo-900/10 z-0"></div>
                        <div className="absolute -top-14 -right-14 w-32 h-32 rounded-full bg-blue-500/10 backdrop-blur-3xl"></div>
                        <CardHeader className="relative z-10 border-b border-blue-200/10">
                          <div className="flex items-center gap-2">
                            <div className="w-10 h-10 rounded-full bg-blue-500/20 backdrop-blur-sm flex items-center justify-center">
                              <Clock className="h-5 w-5 text-blue-400" />
                            </div>
                            <div>
                              <CardTitle>Cache Settings</CardTitle>
                              <CardDescription>Configure data caching behavior</CardDescription>
                            </div>
                          </div>
                        </CardHeader>
                      </div>
                      <CardContent className="space-y-4 relative z-10 pt-6">
                        <p className="text-sm text-muted-foreground">
                          Set how long data is stored in memory before requesting fresh data from DVR
                        </p>

                        <div className="grid gap-4">
                          <div className="space-y-2">
                            <Label htmlFor="channel_cache_ttl" className="flex items-center gap-1">
                              Channel Cache TTL (seconds)
                            </Label>
                            <Input
                              id="channel_cache_ttl"
                              type="number"
                              min="0"
                              {...register("channel_cache_ttl", { valueAsNumber: true })}
                            />
                            <p className="text-xs text-muted-foreground">
                              Time between channel list refreshes
                            </p>
                          </div>

                          <div className="space-y-2">
                            <Label htmlFor="program_cache_ttl">Program Cache TTL (seconds)</Label>
                            <Input
                              id="program_cache_ttl"
                              type="number"
                              min="0"
                              {...register("program_cache_ttl", { valueAsNumber: true })}
                            />
                            <p className="text-xs text-muted-foreground">
                              Time between program guide refreshes
                            </p>
                          </div>

                          <div className="space-y-2">
                            <Label htmlFor="job_cache_ttl">Job Cache TTL (seconds)</Label>
                            <Input
                              id="job_cache_ttl"
                              type="number"
                              min="0"
                              {...register("job_cache_ttl", { valueAsNumber: true })}
                            />
                            <p className="text-xs text-muted-foreground">
                              Time between recording job status refreshes
                            </p>
                          </div>

                          <div className="space-y-2">
                            <Label htmlFor="vod_cache_ttl">VOD Cache TTL (seconds)</Label>
                            <Input
                              id="vod_cache_ttl"
                              type="number"
                              min="0"
                              {...register("vod_cache_ttl", { valueAsNumber: true })}
                            />
                            <p className="text-xs text-muted-foreground">
                              Time between VOD library refreshes
                            </p>
                          </div>
                        </div>
                      </CardContent>
                    </Card>

                    <Card className="overflow-hidden border-blue-400/20 dark:border-blue-500/20 shadow-lg dark:shadow-blue-900/10">
                      <div className="relative">
                        <div className="absolute inset-0 bg-gradient-to-r from-blue-900/10 to-indigo-900/10 z-0"></div>
                        <div className="absolute -top-14 -right-14 w-32 h-32 rounded-full bg-blue-500/10 backdrop-blur-3xl"></div>
                        <CardHeader className="relative z-10 border-b border-blue-200/10">
                          <div className="flex items-center gap-2">
                            <div className="w-10 h-10 rounded-full bg-blue-500/20 backdrop-blur-sm flex items-center justify-center">
                              <HardDrive className="h-5 w-5 text-blue-400" />
                            </div>
                            <div>
                              <CardTitle>Disk Space Monitoring</CardTitle>
                              <CardDescription>Set thresholds for low disk space warnings</CardDescription>
                            </div>
                          </div>
                        </CardHeader>
                      </div>
                      <CardContent className="space-y-4 relative z-10 pt-6">
                        <div className="flex flex-row items-center justify-between p-4 mb-4 rounded-xl border border-blue-400/20 bg-blue-500/5 backdrop-blur-sm shadow-sm transition-colors hover:bg-blue-500/10">
                          <div className="flex items-center gap-3">
                            <div className="flex-shrink-0 w-10 h-10 rounded-full bg-blue-500/20 flex items-center justify-center">
                              <HardDrive className="h-5 w-5 text-blue-400" />
                            </div>
                            <div className="flex flex-col justify-center">
                              <Label htmlFor="alert_disk_space" className="text-base font-medium">
                                Enable Monitoring
                              </Label>
                              <span className="text-xs text-muted-foreground">
                                Activate low disk space alerts
                              </span>
                            </div>
                          </div>
                          <Switch
                            id="alert_disk_space"
                            checked={watch("alert_disk_space")}
                            onCheckedChange={(checked) => {
                              setValue("alert_disk_space", checked, { shouldDirty: true });
                            }}
                            className="data-[state=checked]:bg-blue-600"
                          />
                        </div>
                        
                        <div className={cn(
                          "space-y-4 mt-4 transition-opacity duration-300",
                          !watch("alert_disk_space") && "opacity-50 pointer-events-none"
                        )}>
                          <div className="space-y-2">
                            <Label htmlFor="ds_threshold_percent" className="flex items-center gap-1">
                              Threshold Percentage (% free)
                            </Label>
                            <div className="flex items-center gap-2">
                              <Input
                                id="ds_threshold_percent"
                                type="number"
                                min="0"
                                max="100"
                                {...register("ds_threshold_percent", { valueAsNumber: true })}
                                disabled={!watch("alert_disk_space")}
                              />
                              <span className="text-muted-foreground">%</span>
                            </div>
                            <p className="text-xs text-muted-foreground">
                              Trigger alert when percentage of free space is below this value
                            </p>
                          </div>

                          <div className="space-y-2">
                            <Label htmlFor="ds_threshold_gb" className="flex items-center gap-1">
                              Threshold GB (free)
                              <TooltipProvider>
                                <Tooltip>
                                  <TooltipTrigger asChild>
                                    <Info className="h-3.5 w-3.5 text-muted-foreground cursor-help ml-1" />
                                  </TooltipTrigger>
                                  <TooltipContent side="right">
                                    <p>Alert when free space in GB falls below this value</p>
                                  </TooltipContent>
                                </Tooltip>
                              </TooltipProvider>
                            </Label>
                            <div className="flex items-center gap-2">
                              <Input
                                id="ds_threshold_gb"
                                type="number"
                                min="0"
                                {...register("ds_threshold_gb", { valueAsNumber: true })}
                                disabled={!watch("alert_disk_space")}
                              />
                              <span className="text-muted-foreground">GB</span>
                            </div>
                            <p className="text-xs text-muted-foreground">
                              Trigger alert when free space is below this amount in gigabytes
                            </p>
                          </div>
                        </div>
                      </CardContent>
                    </Card>
                  </div>
                </TabsContent>

                <TabsContent value="notifications" className="space-y-6">
                  <Card className="overflow-hidden border-blue-400/20 dark:border-blue-500/20 shadow-lg dark:shadow-blue-900/10">
                    <div className="relative">
                      <div className="absolute inset-0 bg-gradient-to-r from-blue-900/10 to-indigo-900/10 z-0"></div>
                      <div className="absolute -top-14 -right-14 w-32 h-32 rounded-full bg-blue-500/10 backdrop-blur-3xl"></div>
                      <CardHeader className="relative z-10 border-b border-blue-200/10">
                        <div className="flex items-center gap-2">
                          <div className="w-10 h-10 rounded-full bg-blue-500/20 backdrop-blur-sm flex items-center justify-center">
                            <Bell className="h-5 w-5 text-blue-400" />
                          </div>
                          <div>
                            <CardTitle>Notification Providers</CardTitle>
                            <CardDescription>Configure services to receive notifications</CardDescription>
                          </div>
                        </div>
                      </CardHeader>
                    </div>
                    <CardContent className="space-y-6 relative z-10 pt-6">
                      <Alert className="bg-blue-500/10 border-blue-400/20 text-blue-300">
                        <Info className="h-4 w-4" />
                        <AlertDescription>
                          Configure at least one provider. Enable toggles for services you want to use.
                        </AlertDescription>
                      </Alert>

                      {/* Pushover Card */}
                      <div className="space-y-4">
                        <div className="flex flex-row items-center justify-between p-4 rounded-xl border border-blue-400/20 bg-blue-500/5 backdrop-blur-sm shadow-sm transition-colors hover:bg-blue-500/10">
                          <div className="flex items-center gap-3">
                            <div className="flex-shrink-0 w-10 h-10 rounded-full bg-blue-500/20 flex items-center justify-center">
                              <Bell className="h-5 w-5 text-blue-400" />
                            </div>
                            <div className="flex flex-col justify-center">
                              <span className="text-base font-medium">
                                Pushover
                              </span>
                              <span className="text-xs text-muted-foreground">
                                Notifications for mobile, desktop and browsers
                              </span>
                            </div>
                          </div>
                          <Switch
                            id="pushover-toggle"
                            checked={enabledProviders.pushover}
                            onCheckedChange={(checked) => {
                              setEnabledProviders(prev => ({ ...prev, pushover: checked }));
                              if (!checked) {
                                setValue("pushover_user_key", "", { shouldDirty: true });
                                setValue("pushover_api_token", "", { shouldDirty: true });
                              }
                            }}
                          />
                        </div>

                        {enabledProviders.pushover && (
                          <div className="grid gap-4 md:grid-cols-2 pl-14">
                            <div className="space-y-2">
                              <Label htmlFor="pushover_user_key">User Key</Label>
                              <Input id="pushover_user_key" placeholder="user/group key" {...register("pushover_user_key")} />
                            </div>

                            <div className="space-y-2">
                              <Label htmlFor="pushover_api_token">API Token</Label>
                              <Input id="pushover_api_token" type="password" placeholder="application token" {...register("pushover_api_token")} />
                            </div>
                          </div>
                        )}
                      </div>

                      {/* Apprise Services Title */}
                      <div className="relative flex items-center space-x-2 my-6">
                        <div className="h-px flex-1 bg-gradient-to-r from-transparent to-blue-400/30"></div>
                        <div className="text-blue-400 font-medium text-sm px-3 py-1 rounded-full bg-blue-500/10 backdrop-blur-sm border border-blue-500/20">
                          <div className="flex items-center gap-2">
                            <Share2 className="h-4 w-4" />
                            <span>Apprise Integration</span>
                          </div>
                        </div>
                        <div className="h-px flex-1 bg-gradient-to-l from-transparent to-blue-400/30"></div>
                      </div>

                      {/* Discord Card */}
                      <div className="space-y-4">
                        <div className="flex flex-row items-center justify-between p-4 rounded-xl border border-indigo-400/20 bg-indigo-500/5 backdrop-blur-sm shadow-sm transition-colors hover:bg-indigo-500/10">
                          <div className="flex items-center gap-3">
                            <div className="flex-shrink-0 w-10 h-10 rounded-full bg-indigo-500/20 flex items-center justify-center">
                              {/* Discord icon */}
                              <svg className="h-5 w-5 text-indigo-400" viewBox="0 0 24 24" fill="currentColor">
                                <path d="M20.317 4.3698a19.7913 19.7913 0 00-4.8851-1.5152.0741.0741 0 00-.0785.0371c-.211.3753-.4447.8648-.6083 1.2495-1.8447-.2762-3.68-.2762-5.4868 0-.1636-.3847-.4058-.8742-.6177-1.2495a.077.077 0 00-.0785-.037 19.7363 19.7363 0 00-4.8852 1.515.0699.0699 0 00-.0321.0277C.5334 9.0458-.319 13.5799.0992 18.0578a.0824.0824 0 00.0312.0561c2.0528 1.5076 4.0413 2.4228 5.9929 3.0294a.0777.0777 0 00.0842-.0276c.4616-.6304.8731-1.2952 1.226-1.9942a.076.076 0 00-.0416-.1057c-.6528-.2476-1.2743-.5495-1.8722-.8923a.077.077 0 01-.0076-.1277c.1258-.0943.2517-.1923.3718-.2914a.0743.0743 0 01.0776-.0105c3.9278 1.7933 8.18 1.7933 12.0614 0a.0739.0739 0 01.0785.0095c.1202.099.246.1981.3728.2924a.077.077 0 01-.0066.1276 12.2986 12.2986 0 01-1.873.8914a.0766.0766 0 00-.0407.1067c.3604.698.7719 1.3628 1.225 1.9932a.076.076 0 00.0842.0286c1.961-.6067 3.9495-1.5219 6.0023-3.0294a.077.077 0 00.0313-.0552c.5004-5.177-.8382-9.6739-3.5485-13.6604a.061.061 0 00-.0312-.0286z" />
                              </svg>
                            </div>
                            <div className="flex flex-col justify-center">
                              <span className="text-base font-medium">
                                Discord
                              </span>
                              <span className="text-xs text-muted-foreground">
                                Send notifications to a Discord channel
                              </span>
                            </div>
                          </div>
                          <Switch
                            id="discord-toggle"
                            checked={enabledProviders.discord}
                            onCheckedChange={(checked) => {
                              setEnabledProviders(prev => ({ ...prev, discord: checked }));
                              if (!checked) {
                                setValue("apprise_discord", "", { shouldDirty: true });
                              }
                            }}
                          />
                        </div>

                        {enabledProviders.discord && (
                          <div className="pl-14 space-y-3">
                            <div className="space-y-2">
                              <Label htmlFor="apprise_discord">Webhook URL</Label>
                              <Input
                                id="apprise_discord"
                                placeholder="webhook_id/token"
                                {...register("apprise_discord")}
                              />
                              <p className="text-xs text-muted-foreground">
                                Format: webhook_id/token or full webhook URL
                              </p>
                            </div>
                          </div>
                        )}
                      </div>

                      {/* Telegram Card */}
                      <div className="space-y-4">
                        <div className="flex flex-row items-center justify-between p-4 rounded-xl border border-sky-400/20 bg-sky-500/5 backdrop-blur-sm shadow-sm transition-colors hover:bg-sky-500/10">
                          <div className="flex items-center gap-3">
                            <div className="flex-shrink-0 w-10 h-10 rounded-full bg-sky-500/20 flex items-center justify-center">
                              {/* Telegram icon */}
                              <svg className="h-5 w-5 text-sky-400" viewBox="0 0 24 24" fill="currentColor">
                                <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm4.64 6.8c-.15 1.58-.8 5.42-1.13 7.19-.14.75-.42 1-.68 1.03-.58.05-1.02-.38-1.58-.75-.88-.58-1.38-.94-2.23-1.5-.99-.65-.35-1.01.22-1.59.15-.15 2.71-2.48 2.76-2.69a.2.2 0 00-.05-.18c-.06-.05-.14-.03-.21-.02-.09.02-1.49.95-4.22 2.79-.4.27-.76.41-1.08.4-.36-.01-1.04-.2-1.55-.37-.63-.2-1.12-.31-1.08-.66.02-.18.27-.36.74-.55 2.92-1.27 4.86-2.11 5.83-2.51 2.78-1.16 3.35-1.36 3.73-1.36.08 0 .27.02.39.12.1.08.13.19.14.27-.01.06.01.24 0 .24z" />
                              </svg>
                            </div>
                            <div className="flex flex-col justify-center">
                              <span className="text-base font-medium">
                                Telegram
                              </span>
                              <span className="text-xs text-muted-foreground">
                                Send messages to a Telegram chat
                              </span>
                            </div>
                          </div>
                          <Switch
                            id="telegram-toggle"
                            checked={enabledProviders.telegram}
                            onCheckedChange={(checked) => {
                              setEnabledProviders(prev => ({ ...prev, telegram: checked }));
                              if (!checked) {
                                setValue("apprise_telegram", "", { shouldDirty: true });
                              }
                            }}
                          />
                        </div>

                        {enabledProviders.telegram && (
                          <div className="pl-14 space-y-3">
                            <div className="space-y-2">
                              <Label htmlFor="apprise_telegram">Bot Token / Chat ID</Label>
                              <Input
                                id="apprise_telegram"
                                placeholder="bottoken/ChatID"
                                {...register("apprise_telegram")}
                              />
                              <p className="text-xs text-muted-foreground">
                                Format: bottoken/ChatID (use @BotFather to create a bot)
                              </p>
                            </div>
                          </div>
                        )}
                      </div>

                      {/* Email Card */}
                      <div className="space-y-4">
                        <div className="flex flex-row items-center justify-between p-4 rounded-xl border border-emerald-400/20 bg-emerald-500/5 backdrop-blur-sm shadow-sm transition-colors hover:bg-emerald-500/10">
                          <div className="flex items-center gap-3">
                            <div className="flex-shrink-0 w-10 h-10 rounded-full bg-emerald-500/20 flex items-center justify-center">
                              {/* Email icon */}
                              <svg className="h-5 w-5 text-emerald-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                <path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"></path>
                                <polyline points="22,6 12,13 2,6"></polyline>
                              </svg>
                            </div>
                            <div className="flex flex-col justify-center">
                              <span className="text-base font-medium">
                                Email
                              </span>
                              <span className="text-xs text-muted-foreground">
                                Send email notifications
                              </span>
                            </div>
                          </div>
                          <Switch
                            id="email-toggle"
                            checked={enabledProviders.email}
                            onCheckedChange={(checked) => {
                              setEnabledProviders(prev => ({ ...prev, email: checked }));
                              if (!checked) {
                                setValue("apprise_email", "", { shouldDirty: true });
                                setValue("apprise_email_to", "", { shouldDirty: true });
                              }
                            }}
                          />
                        </div>

                        {enabledProviders.email && (
                          <div className="pl-14 space-y-3">
                            <div className="space-y-2">
                              <Label htmlFor="apprise_email">SMTP Server</Label>
                              <Input
                                id="apprise_email"
                                placeholder="user:password@smtp.domain.com"
                                {...register("apprise_email")}
                              />
                              <p className="text-xs text-muted-foreground">
                                Format: user:password@smtp.domain.com (include port if needed)
                              </p>
                            </div>
                            <div className="space-y-2">
                              <Label htmlFor="apprise_email_to">Recipient Email</Label>
                              <Input
                                id="apprise_email_to"
                                type="email"
                                placeholder="recipient@example.com"
                                {...register("apprise_email_to")}
                              />
                            </div>
                          </div>
                        )}
                      </div>

                      {/* Other Services Toggle */}
                      <div className="mt-6">
                        <details className="group">
                          <summary className="flex cursor-pointer list-none items-center justify-between rounded-lg border border-blue-400/20 bg-blue-500/5 px-4 py-3">
                            <div className="flex items-center gap-2">
                              <Share2 className="h-5 w-5 text-blue-400" />
                              <span className="font-medium">Additional Services</span>
                            </div>
                            <div>
                              <svg 
                                className="h-5 w-5 rotate-0 transform text-blue-400 transition-transform duration-300 ease-in-out group-open:rotate-180" 
                                xmlns="http://www.w3.org/2000/svg" 
                                fill="none" 
                                viewBox="0 0 24 24" 
                                stroke="currentColor"
                              >
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                              </svg>
                            </div>
                          </summary>

                          <div className="mt-4 space-y-4">
                            {/* Slack Card */}
                            <div className="space-y-4">
                              <div className="flex flex-row items-center justify-between p-4 rounded-xl border border-yellow-400/20 bg-yellow-500/5 backdrop-blur-sm shadow-sm transition-colors hover:bg-yellow-500/10">
                                <div className="flex items-center gap-3">
                                  <div className="flex-shrink-0 w-10 h-10 rounded-full bg-yellow-500/20 flex items-center justify-center">
                                    <Share2 className="h-5 w-5 text-yellow-400" />
                                  </div>
                                  <div className="flex flex-col justify-center">
                                    <span className="text-base font-medium">
                                      Slack
                                    </span>
                                    <span className="text-xs text-muted-foreground">
                                      Post to Slack channels
                                    </span>
                                  </div>
                                </div>
                                <Switch
                                  id="slack-toggle"
                                  checked={enabledProviders.slack}
                                  onCheckedChange={(checked) => {
                                    setEnabledProviders(prev => ({ ...prev, slack: checked }));
                                    if (!checked) {
                                      setValue("apprise_slack", "", { shouldDirty: true });
                                    }
                                  }}
                                />
                              </div>

                              {enabledProviders.slack && (
                                <div className="pl-14 space-y-3">
                                  <div className="space-y-2">
                                    <Label htmlFor="apprise_slack">Slack Webhook</Label>
                                    <Input
                                      id="apprise_slack"
                                      placeholder="tokenA/tokenB/tokenC"
                                      {...register("apprise_slack")}
                                    />
                                    <p className="text-xs text-muted-foreground">
                                      Format: tokenA/tokenB/tokenC from webhook URL
                                    </p>
                                  </div>
                                </div>
                              )}
                            </div>

                            {/* Gotify Card */}
                            <div className="space-y-4">
                              <div className="flex flex-row items-center justify-between p-4 rounded-xl border border-purple-400/20 bg-purple-500/5 backdrop-blur-sm shadow-sm transition-colors hover:bg-purple-500/10">
                                <div className="flex items-center gap-3">
                                  <div className="flex-shrink-0 w-10 h-10 rounded-full bg-purple-500/20 flex items-center justify-center">
                                    <Share2 className="h-5 w-5 text-purple-400" />
                                  </div>
                                  <div className="flex flex-col justify-center">
                                    <span className="text-base font-medium">
                                      Gotify
                                    </span>
                                    <span className="text-xs text-muted-foreground">
                                      Self-hosted notification server
                                    </span>
                                  </div>
                                </div>
                                <Switch
                                  id="gotify-toggle"
                                  checked={enabledProviders.gotify}
                                  onCheckedChange={(checked) => {
                                    setEnabledProviders(prev => ({ ...prev, gotify: checked }));
                                    if (!checked) {
                                      setValue("apprise_gotify", "", { shouldDirty: true });
                                    }
                                  }}
                                />
                              </div>

                              {enabledProviders.gotify && (
                                <div className="pl-14 space-y-3">
                                  <div className="space-y-2">
                                    <Label htmlFor="apprise_gotify">Gotify Server URL & Token</Label>
                                    <Input 
                                      id="apprise_gotify" 
                                      placeholder="host.com/token" 
                                      {...register("apprise_gotify")} 
                                    />
                                    <p className="text-xs text-muted-foreground">
                                      Format: host.com/token or full URL
                                    </p>
                                  </div>
                                </div>
                              )}
                            </div>

                            {/* Matrix Card */}
                            <div className="space-y-4">
                              <div className="flex flex-row items-center justify-between p-4 rounded-xl border border-green-400/20 bg-green-500/5 backdrop-blur-sm shadow-sm transition-colors hover:bg-green-500/10">
                                <div className="flex items-center gap-3">
                                  <div className="flex-shrink-0 w-10 h-10 rounded-full bg-green-500/20 flex items-center justify-center">
                                    <Share2 className="h-5 w-5 text-green-400" />
                                  </div>
                                  <div className="flex flex-col justify-center">
                                    <span className="text-base font-medium">
                                      Matrix
                                    </span>
                                    <span className="text-xs text-muted-foreground">
                                      Matrix.org chat platform
                                    </span>
                                  </div>
                                </div>
                                <Switch
                                  id="matrix-toggle"
                                  checked={enabledProviders.matrix}
                                  onCheckedChange={(checked) => {
                                    setEnabledProviders(prev => ({ ...prev, matrix: checked }));
                                    if (!checked) {
                                      setValue("apprise_matrix", "", { shouldDirty: true });
                                    }
                                  }}
                                />
                              </div>

                              {enabledProviders.matrix && (
                                <div className="pl-14 space-y-3">
                                  <div className="space-y-2">
                                    <Label htmlFor="apprise_matrix">Matrix Server & Room</Label>
                                    <Input
                                      id="apprise_matrix"
                                      placeholder="user:pass@domain/#room"
                                      {...register("apprise_matrix")}
                                    />
                                    <p className="text-xs text-muted-foreground">
                                      Format: user:pass@domain/#room
                                    </p>
                                  </div>
                                </div>
                              )}
                            </div>

                            {/* MQTT Card */}
                            <div className="space-y-4">
                              <div className="flex flex-row items-center justify-between p-4 rounded-xl border border-red-400/20 bg-red-500/5 backdrop-blur-sm shadow-sm transition-colors hover:bg-red-500/10">
                                <div className="flex items-center gap-3">
                                  <div className="flex-shrink-0 w-10 h-10 rounded-full bg-red-500/20 flex items-center justify-center">
                                    <Share2 className="h-5 w-5 text-red-400" />
                                  </div>
                                  <div className="flex flex-col justify-center">
                                    <span className="text-base font-medium">
                                      MQTT
                                    </span>
                                    <span className="text-xs text-muted-foreground">
                                      IoT messaging protocol
                                    </span>
                                  </div>
                                </div>
                                <Switch
                                  id="mqtt-toggle"
                                  checked={enabledProviders.mqtt}
                                  onCheckedChange={(checked) => {
                                    setEnabledProviders(prev => ({ ...prev, mqtt: checked }));
                                    if (!checked) {
                                      setValue("apprise_mqtt", "", { shouldDirty: true });
                                    }
                                  }}
                                />
                              </div>

                              {enabledProviders.mqtt && (
                                <div className="pl-14 space-y-3">
                                  <div className="space-y-2">
                                    <Label htmlFor="apprise_mqtt">MQTT Broker URL</Label>
                                    <Input
                                      id="apprise_mqtt"
                                      placeholder="mqtt://user:pass@host"
                                      {...register("apprise_mqtt")}
                                    />
                                    <p className="text-xs text-muted-foreground">
                                      Format: mqtt://user:pass@host
                                    </p>
                                  </div>
                                </div>
                              )}
                            </div>

                            {/* Custom URL Card */}
                            <div className="space-y-4">
                              <div className="flex flex-row items-center justify-between p-4 rounded-xl border border-orange-400/20 bg-orange-500/5 backdrop-blur-sm shadow-sm transition-colors hover:bg-orange-500/10">
                                <div className="flex items-center gap-3">
                                  <div className="flex-shrink-0 w-10 h-10 rounded-full bg-orange-500/20 flex items-center justify-center">
                                    <Share2 className="h-5 w-5 text-orange-400" />
                                  </div>
                                  <div className="flex flex-col justify-center">
                                    <span className="text-base font-medium">
                                      Custom URL
                                    </span>
                                    <span className="text-xs text-muted-foreground">
                                      Any other Apprise-supported service
                                    </span>
                                  </div>
                                </div>
                                <Switch
                                  id="custom-toggle"
                                  checked={enabledProviders.custom}
                                  onCheckedChange={(checked) => {
                                    setEnabledProviders(prev => ({ ...prev, custom: checked }));
                                    if (!checked) {
                                      setValue("apprise_custom", "", { shouldDirty: true });
                                    }
                                  }}
                                />
                              </div>

                              {enabledProviders.custom && (
                                <div className="pl-14 space-y-3">
                                  <div className="space-y-2">
                                    <Label htmlFor="apprise_custom">Custom Apprise URL</Label>
                                    <Input 
                                      id="apprise_custom" 
                                      placeholder="service://user:pass@host.com/path" 
                                      {...register("apprise_custom")} 
                                    />
                                    <p className="text-xs text-muted-foreground">
                                      For services not listed above or complex URLs. See{" "}
                                      <a
                                        href="https://github.com/caronc/apprise/wiki"
                                        target="_blank"
                                        className="underline"
                                        rel="noreferrer"
                                      >
                                        Apprise Wiki
                                      </a>{" "}
                                      for supported services.
                                    </p>
                                  </div>
                                </div>
                              )}
                            </div>
                          </div>
                        </details>
                      </div>
                    </CardContent>
                  </Card>
                </TabsContent>
              </div>
            </Tabs>
          </div>
        </form>
      </div>
      
      {/* Bottom fixed bar for buttons - only show when needed */}
      <div className="fixed bottom-0 left-0 right-0 bg-background border-t border-border p-1 sm:p-3 flex justify-center z-10">
        <div className="container max-w-5xl mx-auto flex justify-end gap-1 sm:gap-3">
          {isDirty && (
            <Badge
              variant="outline"
              className="bg-amber-50 text-amber-700 dark:bg-amber-900 dark:text-amber-300 border-amber-200 dark:border-amber-800 text-[10px] sm:text-xs self-center mr-1 sm:mr-2"
            >
              Unsaved changes
            </Badge>
          )}
          <Button 
            type="button" 
            variant="outline" 
            onClick={() => reset()} 
            disabled={!isDirty}
            size="sm"
            className="h-7 sm:h-10 px-2 sm:px-4 text-xs sm:text-sm"
          >
            <RefreshCw className="mr-1 sm:mr-2 h-3 w-3 sm:h-4 sm:w-4" />
            Discard
          </Button>
          <Button 
            onClick={handleSubmit(onSubmit)} 
            disabled={isSubmitting || !isDirty}
            size="sm"
            className="h-7 sm:h-10 px-2 sm:px-4 text-xs sm:text-sm"
          >
            {isSubmitting ? (
              <>
                <Loader2 className="mr-1 sm:mr-2 h-3 w-3 sm:h-4 sm:w-4 animate-spin" />
                Saving...
              </>
            ) : (
              <>
                <Save className="mr-1 sm:mr-2 h-3 w-3 sm:h-4 sm:w-4" />
                Save Settings
              </>
            )}
          </Button>
        </div>
      </div>
    </div>
  )
}


