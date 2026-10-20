// Trazas extendidas por peticion del asistente (un JSON por request) en Blob Storage.
// Ruta: {cliente}/{proyecto}/{conversacion}/{fecha}_{request_id}.json

param storageAccountName string

@description('Contenedor privado para las trazas.')
param containerName string = 'rag-traces'

@description('Identidades administradas que escriben trazas (Web App).')
param writerPrincipalIds array = []

@description('Dias que se conservan las trazas antes de borrarse automaticamente.')
@minValue(1)
param retentionDays int = 90

// Storage Blob Data Contributor
var blobDataContributorRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storageAccount
  name: 'default'
}

resource container 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: containerName
  properties: {
    publicAccess: 'None'
  }
}

resource lifecycle 'Microsoft.Storage/storageAccounts/managementPolicies@2023-05-01' = {
  parent: storageAccount
  name: 'default'
  properties: {
    policy: {
      rules: [
        {
          name: 'expire-rag-traces'
          enabled: true
          type: 'Lifecycle'
          definition: {
            filters: {
              blobTypes: [
                'blockBlob'
              ]
              prefixMatch: [
                '${containerName}/'
              ]
            }
            actions: {
              baseBlob: {
                delete: {
                  daysAfterModificationGreaterThan: retentionDays
                }
              }
            }
          }
        }
      ]
    }
  }
}

resource writers 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for principalId in writerPrincipalIds: {
  name: guid(container.id, principalId, blobDataContributorRoleId)
  scope: container
  properties: {
    roleDefinitionId: blobDataContributorRoleId
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}]

output containerName string = container.name
output blobEndpoint string = storageAccount.properties.primaryEndpoints.blob
