param planName string
param webAppName string
param location string
param allowedOrigins string
param appSettings array

var allowedOriginsArray = empty(allowedOrigins) ? [] : split(allowedOrigins, ',')
var normalizedAppSettings = [for setting in appSettings: {
  name: setting.name
  value: string(setting.value)
}]
var startupCommand = 'python3 -m gunicorn -w 2 -k uvicorn.workers.UvicornWorker -b 0.0.0.0:$PORT --timeout 600 --access-logfile - --error-logfile - --chdir app-service main:app'

resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: planName
  location: location
  kind: 'linux'
  sku: {
    name: 'B1'
    tier: 'Basic'
    capacity: 1
  }
  properties: {
    reserved: true
  }
}

resource webApp 'Microsoft.Web/sites@2023-12-01' = {
  name: webAppName
  location: location
  kind: 'app,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.12'
      healthCheckPath: '/api/health'
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      cors: {
        allowedOrigins: allowedOriginsArray
      }
      appCommandLine: startupCommand
      appSettings: normalizedAppSettings
    }
  }
}

output webAppName string = webApp.name
output defaultHostName string = webApp.properties.defaultHostName
output principalId string = webApp.identity.principalId
