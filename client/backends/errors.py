"""Excepciones del subsistema de backends."""


class ConfigError(Exception):
    """Error de configuración de backend (SO no soportado, backend desconocido, etc.)."""
