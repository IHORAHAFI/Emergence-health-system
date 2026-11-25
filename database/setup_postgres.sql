-- Run in psql as the postgres superuser:  psql -U postgres -f setup_postgres.sql
CREATE USER emergency_user WITH PASSWORD 'change_me';
CREATE DATABASE emergency_db OWNER emergency_user ENCODING 'UTF8';
GRANT ALL PRIVILEGES ON DATABASE emergency_db TO emergency_user;
-- Tables are created by Django: python manage.py migrate
