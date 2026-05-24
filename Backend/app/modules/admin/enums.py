"""Module 15 — Enums admin."""
from enum import StrEnum


class SettingType(StrEnum):
    """Typage stocké pour un PlatformSetting.

    Le service valide à l'écriture que la valeur Python correspond bien
    au type déclaré, et coerce à la lecture.
    """
    BOOLEAN = "boolean"
    INT = "int"
    FLOAT = "float"
    STRING = "string"
    JSON = "json"


class SettingChangeKind(StrEnum):
    """Type d'entité visée par un SettingChangeLog."""
    SETTING = "SETTING"
    FEATURE_FLAG = "FEATURE_FLAG"
