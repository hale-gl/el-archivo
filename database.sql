-- Esquema de base de datos para El Archivo
-- PostgreSQL / pgAdmin

-- En pgAdmin crea primero la base de datos "el_archivo" si no existe.
-- Luego ejecuta este archivo dentro de esa base.

CREATE TABLE IF NOT EXISTS catalog (
    id VARCHAR(50) PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    image TEXT,
    link TEXT,
    category VARCHAR(20) NOT NULL CHECK (category IN ('series', 'pelicula', 'drama', 'anime', 'lectura')),
    subtype VARCHAR(20) CHECK (subtype IS NULL OR subtype IN ('manga', 'manhwa')),
    status VARCHAR(20) DEFAULT 'pendiente' CHECK (status IN ('pendiente', 'en-curso', 'completado')),
    who VARCHAR(10) DEFAULT '',
    seasons JSONB,
    volumes JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_catalog_category ON catalog (category);
CREATE INDEX IF NOT EXISTS idx_catalog_status ON catalog (status);
CREATE INDEX IF NOT EXISTS idx_catalog_who ON catalog (who);
CREATE INDEX IF NOT EXISTS idx_catalog_updated ON catalog (updated_at);

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(80) UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    display_name VARCHAR(120),
    role VARCHAR(20) DEFAULT 'user' CHECK (role IN ('admin', 'user')),
    active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(20) DEFAULT 'user';

CREATE INDEX IF NOT EXISTS idx_users_username ON users (username);
CREATE INDEX IF NOT EXISTS idx_users_active ON users (active);

CREATE TABLE IF NOT EXISTS covers (
    category VARCHAR(20) PRIMARY KEY,
    image_url TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO covers (category, image_url) VALUES
('series', ''),
('pelicula', ''),
('drama', ''),
('anime', ''),
('lectura', '')
ON CONFLICT (category) DO NOTHING;
