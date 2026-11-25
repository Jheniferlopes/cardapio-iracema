"""Entrada WSGI para hospedagem com Gunicorn."""
from app import app, init_db

# Garante que o banco e as tabelas existam quando o servidor iniciar.
init_db()
