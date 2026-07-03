# El Archivo

Catalogo privado para organizar series, peliculas, anime, manga y manhwa por perfil, estado y progreso.

## Requisitos

- Python 3.11 o superior
- PostgreSQL
- pgAdmin opcional

## Configuracion local

1. Copia `.env.example` como `.env`.
2. Ajusta tus datos de PostgreSQL en `.env`.
3. Instala dependencias:

```bash
pip install -r requirements.txt
```

4. Inicia la web:

```bash
python server.py
```

En Windows tambien puedes ejecutar:

```powershell
.\iniciar-web-python.ps1
```

La app queda en:

```text
http://localhost:3000
```

## Login

Define estos valores en `.env`:

```env
ADMIN_USER=admin
ADMIN_PASSWORD=elige_una_contrasena
SESSION_SECRET=clave_larga_aleatoria
```

Al iniciar, el servidor crea el usuario admin si no existe.

## Base de datos

El servidor crea la base y tablas automaticamente si tiene permisos. Si prefieres hacerlo manual, crea la base en pgAdmin y ejecuta `database.sql`.

## Despliegue

El archivo `render.yaml` esta preparado para Render. En Render debes configurar los secretos `ADMIN_USER`, `ADMIN_PASSWORD` y `SESSION_SECRET`.

No compartas tu `.env`.
