#!/usr/bin/env python3
"""
ByteHawks Distillery CLI - Configuration Testing
"""

import sys
import logging
from pathlib import Path

from scripts.config.loader import load_config
from scripts.core.exceptions import ConfigurationError

# Setup logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('distillery')


def test_config_loading():
    """Test configuration loading and validation"""

    print("=" * 60)
    print("ByteHawks Distillery - Configuration Test")
    print("=" * 60)
    print()

    try:
        # Load configuration
        print("Loading configuration...")
        config = load_config()

        print("✅   Configuration loaded successfully!\n")

        # Display key information
        print("Configuration Summary:")
        print("-" * 60)

        print(f"\n📁   Paths:")
        print(f"    Base:       {config.path.base}")
        print(f"    Downloads:  {config.path.downloads}")
        print(f"    Sources:    {config.path.sources}")
        print(f"    Build:      {config.path.build}")
        print(f"    Libraries:  {config.path.generated.libraries}")
        print(f"    Apps:       {config.path.generated.applications}")

        print(f"\n👤   Build User:")
        if config.build_user:
            print(f"    Name:     {config.build_user.name}")
            print(f"    UID:GID:  {config.build_user.uid}:{config.build_user.gid}")
            print(f"    Home:     {config.build_user.homedir}")

        print(f"\n🏗️   Build Variants:")
        for variant_name in config.list_variants():
            variant = config.get_variant(variant_name)
            print(f"    {variant_name}:")
            print(f"      Alpine:  {variant.metadata.alpine_version}")
            print(f"      musl:    {variant.metadata.musl_version}")
            print(f"      Image:   {variant.get_resolved_image()}")

        print(f"\n🐳   Primary Registry:")
        reg_name, reg_endpoint = config.get_primary_registry()
        print(f"    Type:      {reg_name}")
        print(f"    URL:       {reg_endpoint.url}")
        print(f"    Namespace: {reg_endpoint.namespace}")

        print(f"\n📦   Primary Repository:")
        repo_name, repo_endpoint = config.get_primary_repository()
        print(f"    Type: {repo_name}")
        print(f"    URL:  {repo_endpoint.url}")

        print(f"\n⚙️   Defaults:")
        print(f"    Variant:            {config.defaults.build.variant}")
        print(f"    Architecture:       {config.defaults.build.arch}")
        print(f"    Cleanup on success: {config.defaults.build.cleanup_on_success}")
        print(f"    Generate SBOM:      {config.defaults.packaging.generate_sbom}")

        print("\n" + "=" * 60)
        print("✅   All checks passed!")
        print("=" * 60)

        return 0

    except ConfigurationError as e:
        print(f"\n❌ Configuration Error: {e}")
        return 1

    except Exception as e:
        print(f"\n❌ Unexpected Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(test_config_loading())