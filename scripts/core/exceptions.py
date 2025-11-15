"""
Custom exceptions for ByteHawks Distillery.
"""


class DistilleryError(Exception):
    """Base exception for all Distillery errors"""
    pass


class ConfigurationError(DistilleryError):
    """Raised when configuration is invalid"""
    pass


class TemplateError(DistilleryError):
    """Raised when template rendering fails"""
    pass


class RegistryError(DistilleryError):
    """Raised when registry operations fail"""
    pass


class RepositoryError(DistilleryError):
    """Raised when repository operations fail"""
    pass


class BuildError(DistilleryError):
    """Raised when build operations fail"""
    pass


class DependencyError(DistilleryError):
    """Raised when dependency resolution fails"""
    pass