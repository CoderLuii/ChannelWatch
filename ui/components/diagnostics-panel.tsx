"use client"

import { useState, useEffect } from "react"
import { Button } from "@/components/base/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/base/card"
import { Badge } from "@/components/base/badge"
import { Progress } from "@/components/base/progress"
import { Loader2, AlertCircle, CheckCircle, Activity, Server, Zap, Wrench, Gauge, Wifi, Database, Clock } from "lucide-react"
import { runTest, fetchSystemInfo } from "@/lib/api"
import { useToast } from "@/hooks/use-toast"

interface SystemInfo {
  channelwatch_version: string;
  channels_dvr_host: string | null;
  channels_dvr_port: number;
  channels_dvr_server_version: string | null;
  timezone: string;
  disk_usage_percent: number | null;
  disk_usage_gb: number | null;
  disk_total_gb: number | null;
  disk_free_gb: number | null;
  log_retention_days: number | null;
}

export function DiagnosticsPanel() {
  const [isRunning, setIsRunning] = useState<Record<string, boolean>>({})
  const [results, setResults] = useState<Record<string, { success: boolean; message: string }>>({})
  const [systemInfo, setSystemInfo] = useState<SystemInfo | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const { toast } = useToast()

  
  useEffect(() => {
    const getSystemInfo = async () => {
      try {
        setLoading(true)
        const info = await fetchSystemInfo()
        setSystemInfo(info)
        setError(null)
      } catch (err) {
        console.error("Failed to fetch system info:", err)
        setError(err instanceof Error ? err.message : "Failed to load system information")
        toast({
          title: "Error",
          description: "Failed to load system information. Please check your connection.",
          variant: "destructive",
        })
      } finally {
        setLoading(false)
      }
    }

    getSystemInfo()
  }, [toast])

  const tests = [
    {
      name: "Test Connectivity",
      description: "Check connection to Channels DVR",
      icon: Wifi,
      category: "connectivity",
    },
    {
      name: "Test API Endpoints",
      description: "Verify API endpoints are accessible",
      icon: Server,
      category: "connectivity",
    },
    {
      name: "Test Channel Watching Alert",
      description: "Send a test channel watching alert",
      icon: Activity,
      category: "notifications",
    },
    {
      name: "Test VOD Watching Alert",
      description: "Send a test VOD watching alert",
      icon: Activity,
      category: "notifications",
    },
    {
      name: "Test Disk Space Alert",
      description: "Send a test disk space alert",
      icon: Gauge,
      category: "notifications",
    },
    {
      name: "Test Recording Events Alert",
      description: "Send a test recording event alert",
      icon: Activity,
      category: "notifications",
    },
  ]

  const handleRunTest = async (testName: string) => {
    setIsRunning((prev) => ({ ...prev, [testName]: true }))
    
    
    setResults((prev) => {
      const { [testName]: _, ...rest } = prev; 
      return rest; 
    });

    try {
      const result = await runTest(testName)
      
      
      setResults((prev) => ({
        ...prev,
        [testName]: {
          success: result.success,
          message: result.message,
        },
      }))
      
      
      if (result.success) {
        setTimeout(() => {
          setResults((prev) => {
            
            if (prev[testName]?.message === result.message) {
                const { [testName]: _, ...rest } = prev;
                return rest;
            }
            return prev; 
          });
        }, 10000); 
      }

      toast({
        title: `Test: ${testName}`,
        description: result.message,
        variant: result.success ? "default" : "destructive",
      })
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : "An unknown error occurred";
      
      setResults((prev) => ({
        ...prev,
        [testName]: {
          success: false,
          message: errorMessage,
        },
      }))

      
      setTimeout(() => {
        setResults((prev) => {
           
           if (prev[testName]?.message === errorMessage && !prev[testName]?.success) {
               const { [testName]: _, ...rest } = prev;
               return rest;
           }
           return prev; 
        });
      }, 10000); 

      toast({
        title: "Test Error",
        description: errorMessage,
        variant: "destructive",
      })
    } finally {
      setIsRunning((prev) => ({ ...prev, [testName]: false }))
    }
  }

  
  const formatDiskSize = (sizeInGB: number | null | undefined): string => {
    if (sizeInGB === null || sizeInGB === undefined) {
      return "N/A";
    }
    
    if (sizeInGB >= 1000) {
      return (sizeInGB / 1000).toFixed(2) + " TB";
    } else {
      return Math.round(sizeInGB) + " GB";
    }
  }

  
  const calculateDiskUsage = () => {
    if (!systemInfo || systemInfo.disk_total_gb === null || systemInfo.disk_free_gb === null) {
      return {
        usedGB: null,
        usedTB: "N/A",
        totalGB: null,
        totalTB: "N/A",
        freeGB: null
      };
    }
    
    
    const totalGB = systemInfo.disk_total_gb;
    const freeGB = systemInfo.disk_free_gb;
    const usedGB = totalGB - freeGB;
    
    
    const totalTB = (totalGB / 1000).toFixed(2);
    const usedTB = (usedGB / 1000).toFixed(2);
    
    
    const totalTBFormatted = `${totalTB} TB`;
    const usedTBFormatted = `${usedTB} TB`;
    
    return { 
      usedGB, 
      usedTB: usedTBFormatted, 
      totalGB, 
      totalTB: totalTBFormatted, 
      freeGB,
      usedPercent: systemInfo.disk_usage_percent
    };
  }

  
  const getDiskStatusColor = (usedPercent: number): string => {
    if (usedPercent > 90) return "bg-red-500";
    if (usedPercent > 75) return "bg-amber-500";
    return "bg-green-500";
  }

  
  const diskInfo = calculateDiskUsage();

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-2">
        <h1 className="text-3xl font-bold tracking-tight">Diagnostics</h1>
        <p className="text-muted-foreground">
          Run tests to verify your ChannelWatch configuration and troubleshoot issues.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Activity className="h-5 w-5 text-primary" />
            Diagnostic Tests
          </CardTitle>
          <CardDescription>
            Run tests to verify your ChannelWatch configuration. Results will also appear in the container logs.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid gap-4 md:grid-cols-2">
            {tests.map((test) => (
              <Card key={test.name} className="overflow-hidden border-muted">
                <div className="bg-muted/40 p-4 flex items-start gap-3">
                  <div className="rounded-full bg-primary/10 p-2 flex-shrink-0">
                    <test.icon className="h-4 w-4 text-primary" />
                  </div>
                  <div className="flex-1">
                    <h3 className="font-medium">{test.name}</h3>
                    <p className="text-sm text-muted-foreground">{test.description}</p>
                  </div>
                  <Badge variant="outline" className="flex-shrink-0">
                    {test.category}
                  </Badge>
                </div>

                <div className="p-4">
                  {results[test.name] && (
                    <div className="mb-4">
                      <div
                        className={`flex items-center gap-2 mb-1 ${results[test.name].success ? "text-green-500" : "text-red-500"}`}
                      >
                        {results[test.name].success ? (
                          <CheckCircle className="h-4 w-4" />
                        ) : (
                          <AlertCircle className="h-4 w-4" />
                        )}
                        <span className="font-medium">{results[test.name].success ? "Success" : "Error"}</span>
                      </div>
                      <p className="text-sm text-muted-foreground">{results[test.name].message}</p>

                      {results[test.name].success ? (
                        <Progress value={100} className="h-1 mt-2" indicatorClassName="bg-green-500" />
                      ) : (
                        <Progress value={100} className="h-1 mt-2" indicatorClassName="bg-red-500" />
                      )}
                    </div>
                  )}

                  <Button
                    onClick={() => handleRunTest(test.name)}
                    disabled={isRunning[test.name]}
                    className="w-full"
                    variant={results[test.name]?.success ? "outline" : "default"}
                  >
                    {isRunning[test.name] && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                    {results[test.name]?.success ? "Run Again" : "Run Test"}
                  </Button>
                </div>
              </Card>
            ))}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Server className="h-5 w-5 text-primary" />
            System Information
          </CardTitle>
          <CardDescription>Current system status and configuration details</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-4">
              <div>
                <h3 className="text-sm font-medium mb-2">Environment</h3>
                <div className="bg-muted/40 rounded-lg p-3 space-y-2">
                  {loading ? (
                    <div className="flex items-center justify-center py-4">
                      <Loader2 className="h-5 w-5 animate-spin mr-2" />
                      <span>Loading system information...</span>
                    </div>
                  ) : error ? (
                    <div className="text-red-500 py-2">
                      <AlertCircle className="h-4 w-4 inline mr-2" />
                      {error}
                    </div>
                  ) : (
                    <>
                      <div className="flex justify-between text-sm">
                        <span className="text-muted-foreground">ChannelWatch Version:</span>
                        <span>{systemInfo?.channelwatch_version || "Unknown"}</span>
                      </div>
                      <div className="flex justify-between text-sm">
                        <span className="text-muted-foreground">Channels DVR Server Version:</span>
                        <span>{systemInfo?.channels_dvr_server_version || "Unknown"}</span>
                      </div>
                      <div className="flex justify-between text-sm">
                        <span className="text-muted-foreground">Timezone:</span>
                        <span>{systemInfo?.timezone || "Unknown"}</span>
                      </div>
                    </>
                  )}
                </div>
              </div>
            </div>

            <div className="space-y-4">
              <div>
                <h3 className="text-sm font-medium mb-2">Connection</h3>
                <div className="bg-muted/40 rounded-lg p-3 space-y-2">
                  {loading ? (
                    <div className="flex items-center justify-center py-4">
                      <Loader2 className="h-5 w-5 animate-spin mr-2" />
                      <span>Loading connection details...</span>
                    </div>
                  ) : error ? (
                    <div className="text-red-500 py-2">
                      <AlertCircle className="h-4 w-4 inline mr-2" />
                      Failed to load connection details
                    </div>
                  ) : (
                    <>
                      <div className="flex justify-between text-sm">
                        <span className="text-muted-foreground">Channels DVR Host:</span>
                        <span>{systemInfo?.channels_dvr_host || "Not configured"}</span>
                      </div>
                      <div className="flex justify-between text-sm">
                        <span className="text-muted-foreground">Channels DVR Port:</span>
                        <span>{systemInfo?.channels_dvr_port || "8089"}</span>
                      </div>
                      <div className="flex justify-between text-sm">
                        <span className="text-muted-foreground">Status:</span>
                        <span className={systemInfo?.channels_dvr_server_version ? "text-green-500 flex items-center" : "text-red-500 flex items-center"}>
                          {systemInfo?.channels_dvr_server_version ? (
                            <>
                              <CheckCircle className="h-3 w-3 mr-1" /> Connected
                            </>
                          ) : (
                            <>
                              <AlertCircle className="h-3 w-3 mr-1" /> Not Connected
                            </>
                          )}
                        </span>
                      </div>
                    </>
                  )}
                </div>
              </div>
            </div>
          </div>
          
          {/* Storage Information */}
          {systemInfo && systemInfo.disk_usage_percent != null && (
            <div className="mt-6">
              <h3 className="text-sm font-medium mb-2">Storage</h3>
              <div className="bg-muted/40 rounded-lg p-3">
                <div className="mb-2">
                  <div className="flex justify-between text-sm mb-1">
                    <span className="text-muted-foreground">Disk Usage:</span>
                    <span>
                      {systemInfo.disk_usage_percent}% Used
                      {diskInfo.usedGB !== null && (
                        <span className="ml-1">
                          ({diskInfo.usedTB} of {diskInfo.totalTB})
                        </span>
                      )}
                    </span>
                  </div>
                  <Progress
                    value={systemInfo.disk_usage_percent}
                    className="h-2"
                    indicatorClassName={getDiskStatusColor(systemInfo.disk_usage_percent)}
                  />
                </div>
                
                <div className="grid grid-cols-2 gap-4 mt-3">
                  <div className="bg-muted/60 rounded p-3 flex flex-col items-center">
                    <div className="flex items-center mb-1 text-primary">
                      <Database className="h-4 w-4 mr-1" />
                      <span className="font-medium text-sm">
                        {diskInfo.freeGB ? formatDiskSize(diskInfo.freeGB) : "N/A"}
                      </span>
                    </div>
                    <span className="text-xs text-muted-foreground">Free Space</span>
                  </div>
                  
                  <div className="bg-muted/60 rounded p-3 flex flex-col items-center">
                    <div className="flex items-center mb-1 text-primary">
                      <Clock className="h-4 w-4 mr-1" />
                      <span className="font-medium text-sm">
                        {systemInfo.log_retention_days || 7} days
                      </span>
                    </div>
                    <span className="text-xs text-muted-foreground">Log Retention</span>
                  </div>
                </div>
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}


