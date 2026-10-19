param planName string
param indexerFunctionName string
param incidentsFunctionName string
param location string
@secure()
param storageConnectionString string
param appSettings array

var normalizedAppSettings = [for setting in appSettings: {
  name: setting.name
  value: string(setting.value)
}]

resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: planName
  location: location
  kind: 'functionapp'
  sku: {
    name: 'Y1'
    tier: 'Dynamic'
    size: 'Y1'
    family: 'Y'
    capacity: 0
  }
  properties: {
    computeMode: 'Dynamic'
    reserved: true
  }
}

var functionRuntimeSettings = [
  {
    name: 'FUNCTIONS_WORKER_RUNTIME'
    value: 'python'
  }
  {
    name: 'FUNCTIONS_EXTENSION_VERSION'
    value: '~4'
  }
  {
    name: 'AzureWebJobsStorage'
    value: storageConnectionString
  }
]

resource indexerFunction 'Microsoft.Web/sites@2023-12-01' = {
  name: indexerFunctionName
  location: location
  kind: 'functionapp,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    siteConfig: {
      alwaysOn: false
      linuxFxVersion: 'PYTHON|3.12'
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      appSettings: concat(functionRuntimeSettings, normalizedAppSettings)
    }
  }
}

resource incidentsFunction 'Microsoft.Web/sites@2023-12-01' = {
  name: incidentsFunctionName
  location: location
  kind: 'functionapp,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    siteConfig: {
      alwaysOn: false
      linuxFxVersion: 'PYTHON|3.12'
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      appSettings: concat(functionRuntimeSettings, normalizedAppSettings)
    }
  }
}

output indexerFunctionName string = indexerFunction.name
output indexerDefaultHostName string = indexerFunction.properties.defaultHostName
output incidentsFunctionName string = incidentsFunction.name
output incidentsDefaultHostName string = incidentsFunction.properties.defaultHostName
output indexerPrincipalId string = indexerFunction.identity.principalId
output incidentsPrincipalId string = incidentsFunction.identity.principalId
