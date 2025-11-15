"""
Configuration Data Models using Pydantic for validation and type safety.
"""

from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Dict, List, Optional, Literal, Any
from pathlib import Path
from enum import Enum
import os


# ============================================================================
# Enums
# ============================================================================

class BuildRuntime(str, Enum):
    """Container runtime options"""
    DOCKER = "docker"
    PODMAN = "podman"


class PullPolicy(str, Enum):
    """Image pull policy"""
    ALWAYS = "always"
    IF_NOT_PRESENT = "if-not-present"
    NEVER = "never"


class RegistryStrategy(str, Enum):
    """Registry selection strategy"""
    PRIMARY_ONLY = "primary-only"
    PRIMARY_WITH_FALLBACK = "primary-with-fallback"
    ROUND_ROBIN = "round-robin"


class LogLevel(str, Enum):
    """Logging levels"""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class LogFormat(str, Enum):
    """Log output format"""
    JSON = "json"
    TEXT = "text"


# ============================================================================
# Sub-Models (nested configurations)
# ============================================================================

class BuildUser(BaseModel):
    """Build user configuration for container isolation"""
    name: str = Field(..., description="Username for builds")
    group: str = Field(..., description="Group name for builds")
    uid: int = Field(..., ge=1000, description="User ID (>= 1000)")
    gid: int = Field(..., ge=1000, description="Group ID (>= 1000)")
    homedir: str = Field(..., description="Home directory path")
    shell: str = Field(default="/bin/bash", description="User shell")

    model_config = {"frozen": True}  # Pydantic v2 syntax


class TmpfsConfig(BaseModel):
    """Tmpfs mount configuration"""
    enabled: bool = Field(default=True)
    size: str = Field(default="2g", pattern=r"^\d+[kmg]$")
    mount_point: str = Field(default="/tmp")


class FakerootConfig(BaseModel):
    """Fakeroot configuration"""
    enabled: bool = Field(default=True)


class SecurityConfig(BaseModel):
    """Container security options"""
    no_new_privileges: bool = Field(default=True)
    drop_capabilities: List[str] = Field(default_factory=lambda: ["ALL"])
    add_capabilities: List[str] = Field(default_factory=list)


class RuntimeConfig(BaseModel):
    """Container runtime options"""
    tmpfs: TmpfsConfig = Field(default_factory=TmpfsConfig)
    fakeroot: FakerootConfig = Field(default_factory=FakerootConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)


class PathConfig(BaseModel):
    """Filesystem path configuration"""
    base: str = Field(..., description="Base path for all artifacts")
    downloads: str = Field(..., description="Download cache directory")
    sources: str = Field(..., description="Source code directory")
    build: str = Field(..., description="Build working directory")

    class GeneratedPaths(BaseModel):
        """Output paths for generated artifacts"""
        libraries: str = Field(..., description="Compiled libraries path")
        applications: str = Field(..., description="Applications path template")

    generated: GeneratedPaths

    @field_validator('*', mode='before')  # Pydantic v2 syntax
    @classmethod
    def expand_paths(cls, v):
        """Expand ~ and environment variables in paths"""
        if isinstance(v, str):
            return str(Path(v).expanduser())
        return v


class VariantMetadata(BaseModel):
    """Metadata for a build variant"""
    alpine_version: str = Field(..., pattern=r"^\d+\.\d+$")
    musl_version: str = Field(..., pattern=r"^\d+\.\d+\.\d+$")
    kernel: str = Field(..., pattern=r"^\d+\.\d+$")
    arch: str = Field(default="amd64")


class BuildVariant(BaseModel):
    """Build variant configuration (stable, legacy, etc)"""
    image: str = Field(..., description="Container image template")
    metadata: VariantMetadata
    description: Optional[str] = Field(None, description="Human-readable description")
    support_until: Optional[str] = Field(None, description="Support end date (YYYY-MM-DD)")

    # Cached resolved image (populated after template rendering)
    _resolved_image: Optional[str] = None

    def set_resolved_image(self, resolved: str):
        """Set the resolved image after template rendering"""
        self._resolved_image = resolved

    def get_resolved_image(self) -> Optional[str]:
        """Get the resolved image if available"""
        return self._resolved_image


class ContainerBuildType(BaseModel):
    """Container build type configuration"""
    image_basename: str = Field(default="builda-bar")
    runtime: BuildRuntime = Field(default=BuildRuntime.DOCKER)
    pull_policy: PullPolicy = Field(default=PullPolicy.IF_NOT_PRESENT)


class BuildTypeConfig(BaseModel):
    """Build type configuration (currently only container)"""
    container: ContainerBuildType = Field(default_factory=ContainerBuildType)


class RegistryEndpoint(BaseModel):
    """Registry endpoint configuration"""
    public: bool = Field(default=True)
    url: str = Field(..., description="Registry URL")
    namespace: str = Field(..., description="Registry namespace/project")
    username: Optional[str] = Field(None, description="Username (supports env vars)")
    password: Optional[str] = Field(None, description="Password (supports env vars)")
    token: Optional[str] = Field(None, description="Auth token (supports env vars)")
    timeout: int = Field(default=30, ge=1, description="Request timeout in seconds")
    retry: int = Field(default=3, ge=0, description="Number of retries")

    @field_validator('username', 'password', 'token', mode='before')  # Pydantic v2
    @classmethod
    def expand_env_vars(cls, v):
        """Expand environment variables like ${VAR_NAME}"""
        if v and isinstance(v, str) and v.startswith('${') and v.endswith('}'):
            var_name = v[2:-1]
            return os.getenv(var_name)
        return v


class RegistryConfig(BaseModel):
    """Registry configuration for container images"""
    strategy: RegistryStrategy = Field(default=RegistryStrategy.PRIMARY_WITH_FALLBACK)
    primary: Dict[str, RegistryEndpoint]
    fallback: Optional[Dict[str, RegistryEndpoint]] = None

    @model_validator(mode='after')  # Pydantic v2 syntax
    def validate_endpoints(self):
        """Ensure at least one endpoint exists"""
        if not self.primary:
            raise ValueError("At least one primary registry must be configured")
        return self


from typing import Union, Literal
from pydantic import Field, BaseModel, field_validator, model_validator


class NexusRepositoryEndpoint(BaseModel):
    """Nexus/Artifactory style repository"""
    type: Literal["nexus"] = "nexus"
    url: str = Field(..., description="Repository URL")
    repository: str = Field(..., description="Repository name")
    path_template: str = Field(default="{package}/{major_minor}/{full_version}")
    username: Optional[str] = None
    password: Optional[str] = None
    timeout: int = Field(default=60, ge=1)
    retry: int = Field(default=3, ge=0)

    @field_validator('username', 'password', mode='before')
    @classmethod
    def expand_env_vars(cls, v):
        if v and isinstance(v, str) and v.startswith('${') and v.endswith('}'):
            return os.getenv(v[2:-1])
        return v

    def get_base_url(self) -> str:
        return f"{self.url}/{self.repository}"


class S3RepositoryEndpoint(BaseModel):
    """S3 compatible repository"""
    type: Literal["s3"] = "s3"
    bucket: str = Field(..., description="S3 bucket name")
    region: str = Field(..., description="S3 region")
    endpoint: Optional[str] = Field(None, description="Custom S3 endpoint")
    path_template: str = Field(default="{package}/{major_minor}/{full_version}")
    access_key: Optional[str] = None
    secret_key: Optional[str] = None
    timeout: int = Field(default=60, ge=1)
    retry: int = Field(default=3, ge=0)

    @field_validator('access_key', 'secret_key', mode='before')
    @classmethod
    def expand_env_vars(cls, v):
        if v and isinstance(v, str) and v.startswith('${') and v.endswith('}'):
            return os.getenv(v[2:-1])
        return v

    def get_base_url(self) -> str:
        if self.endpoint:
            return f"{self.endpoint}/{self.bucket}"
        return f"https://s3.{self.region}.amazonaws.com/{self.bucket}"


# Union type per repository endpoint
RepositoryEndpoint = Union[NexusRepositoryEndpoint, S3RepositoryEndpoint]


class RepositoryConfig(BaseModel):
    """Artifact repository configuration"""
    strategy: RegistryStrategy = Field(default=RegistryStrategy.PRIMARY_WITH_FALLBACK)
    primary: Dict[str, RepositoryEndpoint]
    fallback: Optional[Dict[str, RepositoryEndpoint]] = None


class RepositoryConfig(BaseModel):
    """Artifact repository configuration"""
    strategy: RegistryStrategy = Field(default=RegistryStrategy.PRIMARY_WITH_FALLBACK)
    primary: Dict[str, RepositoryEndpoint]
    fallback: Optional[Dict[str, RepositoryEndpoint]] = None


class DefaultsConfig(BaseModel):
    """Default values for builds"""

    class BuildDefaults(BaseModel):
        variant: str = Field(default="stable")
        arch: str = Field(default="amd64")
        cleanup_on_success: bool = Field(default=True)
        cleanup_on_failure: bool = Field(default=False)

    class PackagingDefaults(BaseModel):
        format: str = Field(default="tar.gz")
        generate_checksum: bool = Field(default=True)
        generate_sbom: bool = Field(default=True)
        generate_signature: bool = Field(default=False)

    build: BuildDefaults = Field(default_factory=BuildDefaults)
    packaging: PackagingDefaults = Field(default_factory=PackagingDefaults)


class LoggingConfig(BaseModel):
    """Logging configuration"""
    level: LogLevel = Field(default=LogLevel.INFO)
    format: LogFormat = Field(default=LogFormat.JSON)
    output: Literal["stdout", "file"] = Field(default="stdout")
    file: Optional[str] = Field(None, description="Log file path if output=file")


class GitHubConfig(BaseModel):
    """GitHub integration configuration"""
    token: Optional[str] = Field(None, description="GitHub token for API access")
    api_url: str = Field(default="https://api.github.com")

    @field_validator('token', mode='before')
    @classmethod
    def expand_env_vars(cls, v):
        """Expand environment variables"""
        if v and isinstance(v, str) and v.startswith('${') and v.endswith('}'):
            var_name = v[2:-1]
            return os.getenv(var_name)
        return v


# ============================================================================
# Main Configuration Model
# ============================================================================

class DistilleryConfig(BaseModel):
    """
    Main configuration model for ByteHawks Distillery.

    This model represents the complete configuration loaded from config.yaml.
    All nested structures are validated and type-checked.
    """

    # Global variables for templates
    variables: Dict[str, Any] = Field(default_factory=dict)

    # Defaults
    defaults: DefaultsConfig = Field(default_factory=DefaultsConfig)

    # Paths
    path: PathConfig

    # Build options
    option: Dict[str, Any] = Field(default_factory=dict)

    # Parsed structured options
    build_user: Optional[BuildUser] = None
    runtime: Optional[RuntimeConfig] = None

    # Build configuration
    build: Dict[str, Any]

    # Parsed build structures
    build_type: Optional[BuildTypeConfig] = None
    build_variants: Optional[Dict[str, BuildVariant]] = None

    # Registry and repository
    registry: RegistryConfig
    repository: RepositoryConfig

    # Logging
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    # GitHub integration
    github: Optional[GitHubConfig] = Field(default_factory=GitHubConfig)

    model_config = {
        "arbitrary_types_allowed": True,
        "validate_assignment": True
    }

    @model_validator(mode='before')  # ✅ Pydantic v2 syntax
    @classmethod
    def parse_nested_structures(cls, values):
        """Parse nested structures from 'option' and 'build' dicts"""

        # Parse build_user from option
        if 'option' in values and 'build_user' in values['option']:
            values['build_user'] = BuildUser(**values['option']['build_user'])

        # Parse runtime from option
        if 'option' in values and 'runtime' in values['option']:
            values['runtime'] = RuntimeConfig(**values['option']['runtime'])

        # Parse build type
        if 'build' in values and 'type' in values['build']:
            values['build_type'] = BuildTypeConfig(**values['build']['type'])

        # Parse build variants
        if 'build' in values and 'variant' in values['build']:
            variants = {}
            for name, data in values['build']['variant'].items():
                variants[name] = BuildVariant(**data)
            values['build_variants'] = variants

        return values

    def get_variant(self, name: str) -> Optional[BuildVariant]:
        """Get a build variant by name"""
        if self.build_variants:
            return self.build_variants.get(name)
        return None

    def list_variants(self) -> List[str]:
        """List all available variant names"""
        if self.build_variants:
            return list(self.build_variants.keys())
        return []

    def get_primary_registry(self) -> tuple[str, RegistryEndpoint]:
        """Get the primary registry name and endpoint"""
        name = list(self.registry.primary.keys())[0]
        return name, self.registry.primary[name]

    def get_fallback_registry(self) -> Optional[tuple[str, RegistryEndpoint]]:
        """Get the fallback registry name and endpoint"""
        if self.registry.fallback:
            name = list(self.registry.fallback.keys())[0]
            return name, self.registry.fallback[name]
        return None

    def get_primary_repository(self) -> tuple[str, RepositoryEndpoint]:
        """Get the primary artifact repository"""
        name = list(self.repository.primary.keys())[0]
        return name, self.repository.primary[name]

    def get_fallback_repository(self) -> Optional[tuple[str, RepositoryEndpoint]]:
        """Get the fallback artifact repository"""
        if self.repository.fallback:
            name = list(self.repository.fallback.keys())[0]
            return name, self.repository.fallback[name]
        return None


# ============================================================================
# File Envelope (top-level YAML structure)
# ============================================================================

class ConfigFile(BaseModel):
    """
    Top-level configuration file structure.

    This represents the root YAML structure with version/schema metadata.
    """
    version: str = Field(..., pattern=r"^\d+\.\d+\.\d+$", description="Config schema version")
    schema_name: str = Field(  # ✅ Renamed from 'schema' to 'schema_name'
        default="distillery-config",
        description="Schema identifier",
        alias="schema"  # ✅ YAML usa 'schema', ma interno è 'schema_name'
    )
    config: DistilleryConfig

    model_config = {
        "validate_assignment": True,
        "populate_by_name": True  # ✅ Allow both 'schema' and 'schema_name'
    }