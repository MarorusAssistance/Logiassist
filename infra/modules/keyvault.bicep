@description('Nombre del Key Vault (3-24 caracteres, alfanumerico y guiones, unico global).')
param keyVaultName string
param location string

@description('Principal IDs de identidades administradas (Web App y Function Apps) que solo leen secretos.')
param secretReaderPrincipalIds array = []

@description('Object ID del usuario/SP que despliega y sincroniza secretos. Vacio = no se asigna rol.')
param writerPrincipalId string = ''

@allowed([
  'User'
  'ServicePrincipal'
  'Group'
])
param writerPrincipalType string = 'User'

@minValue(7)
@maxValue(90)
param softDeleteRetentionInDays int = 7

// Roles integrados de Azure RBAC para Key Vault.
var keyVaultSecretsUserRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
var keyVaultSecretsOfficerRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7')

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  properties: {
    tenantId: subscription().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: softDeleteRetentionInDays
    // Purge protection no se activa: es irreversible y bloquea recrear la demo con el mismo nombre.
    enabledForDeployment: false
    enabledForTemplateDeployment: false
    enabledForDiskEncryption: false
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      bypass: 'AzureServices'
      defaultAction: 'Allow'
    }
  }
}

resource secretReaders 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for principalId in secretReaderPrincipalIds: {
  name: guid(vault.id, principalId, keyVaultSecretsUserRoleId)
  scope: vault
  properties: {
    roleDefinitionId: keyVaultSecretsUserRoleId
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}]

resource secretWriter 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(writerPrincipalId)) {
  name: guid(vault.id, writerPrincipalId, keyVaultSecretsOfficerRoleId)
  scope: vault
  properties: {
    roleDefinitionId: keyVaultSecretsOfficerRoleId
    principalId: writerPrincipalId
    principalType: writerPrincipalType
  }
}

output keyVaultName string = vault.name
output keyVaultUri string = vault.properties.vaultUri
output keyVaultId string = vault.id
