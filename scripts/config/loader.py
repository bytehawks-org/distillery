"""
Configuration loader with validation and template resolution.
"""

import yaml
from pathlib import Path
from typing import Optional
import logging

from scripts.config.models import ConfigFile, DistilleryConfig
from scripts.config.template_engine import DistilleryTemplateEngine
from scripts.core.exceptions import ConfigurationError

logger = logging.getLogger(__name__)


class ConfigLoader:
    """
    Loads, validates, and resolves ByteHawks configuration files.

    Workflow:
    1. Load YAML file
    2. Validate against Pydantic models
    3. Resolve template variables
    4. Return validated DistilleryConfig object
    """

    def __init__(self, config_path: Optional[Path] = None):
        """
        Initialize config loader.

        Args:
            config_path: Path to config.yaml (default: config/config.yaml)
        """
        if config_path is None:
            # Path relativo alla root del progetto
            config_path = Path(__file__).parent.parent.parent / "config" / "config.yaml"

        self.config_path = Path(config_path)
        self.template_engine = DistilleryTemplateEngine(max_passes=5, strict=True)

    def load(self) -> DistilleryConfig:
        """
        Load and validate configuration.

        Returns:
            Validated DistilleryConfig object

        Raises:
            ConfigurationError: If config is invalid
        """
        logger.info(f"Loading configuration from: {self.config_path}")

        # Step 1: Load YAML
        raw_config = self._load_yaml()

        # Step 2: Validate structure
        config_file = self._validate_structure(raw_config)

        # Step 3: Resolve templates
        resolved_config = self._resolve_templates(config_file.config)

        logger.info("Configuration loaded successfully")
        return resolved_config

    def _load_yaml(self) -> dict:
        """Load YAML file"""
        if not self.config_path.exists():
            raise ConfigurationError(f"Config file not found: {self.config_path}")

        try:
            with open(self.config_path, 'r') as f:
                data = yaml.safe_load(f)

            if not isinstance(data, dict):
                raise ConfigurationError("Config file must contain a YAML dictionary")

            return data

        except yaml.YAMLError as e:
            raise ConfigurationError(f"Invalid YAML syntax: {e}") from e
        except Exception as e:
            raise ConfigurationError(f"Failed to load config: {e}") from e

    def _validate_structure(self, raw_config: dict) -> ConfigFile:
        """Validate config structure using Pydantic models"""
        try:
            config_file = ConfigFile(**raw_config)
            logger.debug(f"Config schema validated: {config_file.schema_name} v{config_file.version}")
            return config_file

        except Exception as e:
            raise ConfigurationError(f"Configuration validation failed: {e}") from e

    def _resolve_templates(self, config: DistilleryConfig) -> DistilleryConfig:
        """
        Resolve all template variables in configuration.

        Template resolution order:
        1. Global variables
        2. Path templates
        3. Build variant images
        """
        logger.debug("Resolving configuration templates...")

        # Build template context
        context = self._build_template_context(config)

        # Resolve path templates FIRST
        config = self._resolve_paths(config, context)

        # Update context with resolved paths (for nested references)
        context['path'] = {
            'base': config.path.base,
            'downloads': config.path.downloads,
            'sources': config.path.sources,
            'build': config.path.build,
        }

        # 3. Resolve variant images
        if config.build_variants:
            for variant_name, variant in config.build_variants.items():
                # Create context with 'this' pointing to current variant
                current_item = {
                    'name': variant_name,
                    'metadata': variant.metadata.model_dump()
                }

                resolved_image = self.template_engine.render(
                    variant.image,
                    context,
                    current_item
                )

                variant.set_resolved_image(resolved_image)
                logger.debug(f"Resolved {variant_name} image: {resolved_image}")

        return config

    def _build_template_context(self, config: DistilleryConfig) -> dict:
        """
        Build global template context.

        Supports both:
        - {{ variables.key }} (namespaced)
        - {{ key }} (flat)
        """
        context = {}

        # Add variables WITH namespace
        if config.variables:
            context['variables'] = dict(config.variables)  # Nested
            context.update(config.variables)  # Also flat

        # Add computed registry variables (both styles)
        if config.registry.primary:
            reg_name, reg_endpoint = config.get_primary_registry()

            # Flat style
            context['registry_url'] = reg_endpoint.url
            context['registry_namespace'] = reg_endpoint.namespace

            # Namespaced style (add to 'variables' if exists)
            if 'variables' not in context:
                context['variables'] = {}
            context['variables']['registry_url'] = reg_endpoint.url
            context['variables']['registry_namespace'] = reg_endpoint.namespace

        # Add build config
        if config.build_type:
            context['build'] = {
                'type': {
                    'container': {
                        'image_basename': config.build_type.container.image_basename
                    }
                }
            }

        # Add path
        context['path'] = {
            'base': config.path.base
        }

        return context

    def _resolve_paths(self, config: DistilleryConfig, context: dict) -> DistilleryConfig:
        """
        Resolve path templates.

        Note: {{ package.name }} will remain unresolved until build time.
        """

        # Resolve base paths (non-package specific)
        if self.template_engine.has_template_vars(config.path.base):
            config.path.base = self.template_engine.render(config.path.base, context)
            logger.debug(f"Resolved path.base: {config.path.base}")

        if self.template_engine.has_template_vars(config.path.downloads):
            config.path.downloads = self.template_engine.render(config.path.downloads, context)
            logger.debug(f"Resolved path.downloads: {config.path.downloads}")

        if self.template_engine.has_template_vars(config.path.sources):
            config.path.sources = self.template_engine.render(config.path.sources, context)
            logger.debug(f"Resolved path.sources: {config.path.sources}")

        if self.template_engine.has_template_vars(config.path.build):
            config.path.build = self.template_engine.render(config.path.build, context)
            logger.debug(f"Resolved path.build: {config.path.build}")

        # Resolve generated paths
        if self.template_engine.has_template_vars(config.path.generated.libraries):
            config.path.generated.libraries = self.template_engine.render(
                config.path.generated.libraries, context
            )
            logger.debug(f"Resolved path.generated.libraries: {config.path.generated.libraries}")

        # Applications path may contain {{ package.name }} - resolve what we can
        if self.template_engine.has_template_vars(config.path.generated.applications):
            try:
                # Try to resolve, but {{ package.name }} will remain
                resolved = self.template_engine.render(
                    config.path.generated.applications, context
                )
                config.path.generated.applications = resolved

                # Log if package.name remains (expected)
                if '{{ package.name }}' in resolved or '{{package.name}}' in resolved:
                    logger.debug(
                        f"Path contains runtime variable: {resolved} "
                        "(will be resolved at build time)"
                    )
                else:
                    logger.debug(f"Resolved path.generated.applications: {resolved}")

            except Exception as e:
                # If template fails (e.g., package.name undefined), keep original
                logger.debug(
                    f"Path contains runtime variables: {config.path.generated.applications} "
                    "(will be resolved at build time)"
                )

        return config


def load_config(config_path: Optional[Path] = None) -> DistilleryConfig:
    """
    Convenience function to load configuration.

    Args:
        config_path: Path to config.yaml

    Returns:
        Validated DistilleryConfig object
    """
    loader = ConfigLoader(config_path)
    return loader.load()