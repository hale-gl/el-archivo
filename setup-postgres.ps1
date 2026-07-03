$ErrorActionPreference = 'Stop'

$psql = 'C:\Program Files\PostgreSQL\18\bin\psql.exe'
$dbName = 'el_archivo'
$user = Read-Host 'Usuario de PostgreSQL' 
if ([string]::IsNullOrWhiteSpace($user)) { $user = 'postgres' }

$securePassword = Read-Host 'Contrasena de PostgreSQL' -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
$plainPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)

$env:PGPASSWORD = $plainPassword

$exists = & $psql -h localhost -p 5432 -U $user -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '$dbName'"
if ($exists.Trim() -ne '1') {
  & $psql -h localhost -p 5432 -U $user -d postgres -c "CREATE DATABASE $dbName WITH ENCODING 'UTF8'"
}

& $psql -h localhost -p 5432 -U $user -d $dbName -f '.\database.sql'

(Get-Content '.\.env') `
  -replace '^DB_USER=.*', "DB_USER=$user" `
  -replace '^DB_PASSWORD=.*', "DB_PASSWORD=$plainPassword" |
  Set-Content '.\.env'

Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
Write-Host "Base de datos '$dbName' lista. Ahora ejecuta: npm install ; npm start"
