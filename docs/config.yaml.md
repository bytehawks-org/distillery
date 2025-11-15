# Configuration Reference

This document explains the available configuration options for the `distillery` component.

Distillery is a component of the larger project called `Bytehawks`, with the main goal of managing all package build steps using a single, human-readable configuration file.

Each section below describes a key in the configuration file, its purpose, and its possible values.


``` yaml# Distillery Configuration File
# Specifies the version of the configuration schema being used.
# This helps in identifying the compatibility of the configuration file with the tool.
version: 1.0.0

# Defines the schema type for the configuration file.
# This is used to validate the structure and content of the configuration.
schema: "distillery-config"

config:
  # Variables section defines reusable values that can be referenced throughout the configuration.
  variables:
    # The URL of the primary container registry used for storing build images.
    primary_registry_url: "registry.bytehawks.org"
    
    # The namespace within the primary registry where images are stored.
    primary_registry_namespace: "bytehawks-org"
    
    # The base path where artifacts will be stored on the filesystem.
    artifact_base_path: "/opt/bytehawks"

  # Defaults section specifies default values for build and packaging configurations.
  defaults:
    build:
      # The default build variant to use defined in build.variant section.
      variant: "stable"
      
      # The default architecture for the build process (in this first version, only amd64 is supported).
      arch: "amd64"
      
      # Whether to clean up temporary files after a successful build.
      cleanup_on_success: true
      
      # Whether to clean up temporary files after a failed build.
      cleanup_on_failure: false

    packaging:
      # The default packaging format for the build artifacts (e.g., tar.gz, zip).
      format: "tar.gz"
      
      # Whether to generate a checksum file for the build artifacts.
      generate_checksum: true
      
      # Whether to generate a Software Bill of Materials (SBOM) for the build artifacts.
      # Roadmap item: in this first version sbom generation is not yet implemented but we
      # added and documented this option to fix a first, stable schema version for
      # production ready use.
      generate_sbom: true
      
      # Whether to generate a cryptographic signature for the build artifacts (planned feature).
      # Roadmap item: in this first version signature generation is not yet implemented but we
      # added and documented this option to fix a first, stable schema version for
      # production ready use.
      generate_signature: true

  # Path section defines various paths used during the build process.
  path:
    # The base path for all other paths, derived from the artifact base path variable.
    base: "{{ variables.artifact_base_path }}"
    
    # The path where downloaded files will be stored temporarily.
    downloads: "{{ path.base }}/tmp"
    
    # The path where source files will be stored.
    sources: "{{ path.base }}/src"
    
    # The path where build artifacts will be stored.
    build: "{{ path.base }}/build"

    generated:
      # The path where generated libraries will be stored.
      libraries: "{{ path.base }}/deps"
      
      # The path where generated applications will be stored, including the package name.
      applications: "{{ path.base }}/apps/{{ package.name }}"

  # Build options
  option:
    build_user:
      # The username of the build user. This user is used during the build process.
      name: "bh"
      
      # The group name associated with the build user.
      group: "bh"
      
      # The user ID (UID) of the build user.
      uid: 1024
      
      # The group ID (GID) of the build user.
      gid: 1024
      
      # The home directory of the build user.
      homedir: "/home/bh"
      
      # The default shell for the build user.
      shell: "/bin/bash"

  runtime:
    tmpfs:
      # Configuration for temporary file systems (tmpfs).
      # Enables the use of a tmpfs mount point during runtime.
      enabled: true
  
      # Specifies the size of the tmpfs mount point.
      # Example: "2g" for 2 gigabytes.
      size: "2g"
  
      # Defines the mount point for the tmpfs.
      # Example: "/tmp" for temporary files.
      mount_point: "/tmp"
  
    fakeroot:
      # Configuration for fakeroot functionality.
      # Enables the use of fakeroot during runtime.
      enabled: true

    security:
      # Ensures that no new privileges can be gained by the process.
      # This is a security feature to prevent privilege escalation.
      no_new_privileges: true
      
      # Specifies a list of capabilities to drop from the process.
      # "ALL" indicates that all capabilities are removed for enhanced security.
      drop_capabilities: ["ALL"]

  # Build configuration
  build:
    type:
      container:
        # The base name of the container image used for the build process.
        image_basename: "builda-bar"
  
        # The runtime environment to use for the container (e.g., Docker or Podman).
        runtime: "docker"  # or "podman"
  
        # The policy for pulling the container image.
        # "if-not-present" means the image will only be pulled if it is not already available locally.
        # Alternatives include "always" and "never".
        pull_policy: "if-not-present"

    variant:
      old-legacy:
        # The container image for the "old-legacy" build variant.
        # This image is constructed using the primary registry URL, namespace, and the container image base name.
        image: "{{ variables.primary_registry_url }}/{{ variables.primary_registry_namespace }}/{{ build.type.container.image_basename }}:{{ this.name }}"
        
        metadata:
          # The version of the Alpine Linux distribution used in this variant.
          alpine_version: "3.15"
          
          # The version of the musl C library used in this variant.
          musl_version: "1.2.2"
          
          # The Linux kernel version associated with this variant.
          kernel: "5.15"
          
          # The architecture supported by this variant (e.g., amd64).
          arch: "amd64"
        
        # A brief description of the "old-legacy" variant, highlighting its purpose.
        description: "Legacy infrastructure with long certification cycles"
        
        # The date until which this variant is supported.
        support_until: "2026-12-31"

      legacy:
        image: "{{ variables.primary_registry_url }}/{{ variables.primary_registry_namespace }}/{{ build.type.container.image_basename }}:{{ this.name }}"
        metadata:
          alpine_version: "3.20"
          musl_version: "1.2.5"
          kernel: "6.6"
          arch: "amd64"
        description: "Long-term stable for critical production"
        support_until: "2028-12-31"

      stable:
        image: "{{ variables.primary_registry_url }}/{{ variables.primary_registry_namespace }}/{{ build.type.container.image_basename }}:{{ this.name }}"
        metadata:
          alpine_version: "3.21"
          musl_version: "1.2.5"
          kernel: "6.6"
          arch: "amd64"
        description: "Current production standard"
        support_until: "2027-12-31"

      stream:
        image: "{{ variables.primary_registry_url }}/{{ variables.primary_registry_namespace }}/{{ build.type.container.image_basename }}:{{ this.name }}"
        metadata:
          alpine_version: "3.22"
          musl_version: "1.2.5"
          kernel: "6.12"
          arch: "amd64"
        description: "Bleeding edge for R&D and testing"
        support_until: "rolling"

  # Container registry configuration for build images.
  registry:
    # Strategy to use for selecting the container registry.
    # "primary-with-fallback" means the primary registry is used first, and the fallback is used if the primary fails.
    strategy: "primary-with-fallback"
  
    primary:
      harbor:
        # Indicates whether the primary registry is publicly accessible.
        public: true
  
        # The URL of the primary container registry.
        url: "registry.bytehawks.org"
  
        # The namespace within the primary registry where images are stored.
        namespace: "bytehawks-org"
  
        # The username for authenticating with the primary registry.
        username: "${HARBOR_USER}"
  
        # The password for authenticating with the primary registry.
        password: "${HARBOR_PASSWORD}"
  
        # Timeout in seconds for operations with the primary registry.
        timeout: 30
  
        # Number of retry attempts for operations with the primary registry.
        retry: 3
  
    fallback:
      ghcr:
        # Indicates whether the fallback registry is publicly accessible.
        public: true
  
        # The URL of the fallback container registry.
        url: "ghcr.io"
  
        # The namespace within the fallback registry where images are stored.
        namespace: "bytehawks-org"
  
        # The username for authenticating with the fallback registry.
        username: "${GITHUB_ACTOR}"
  
        # The token for authenticating with the fallback registry.
        token: "${GITHUB_TOKEN}"
  
        # Timeout in seconds for operations with the fallback registry.
        timeout: 30
  
        # Number of retry attempts for operations with the fallback registry.
        retry: 3

  # Artifact repository configuration for storing compiled binaries.
  repository:
    # Strategy for selecting the artifact repository.
    # "primary-with-fallback" means the primary repository is used first, and the fallback is used if the primary fails.
    strategy: "primary-with-fallback"
  
    primary:
      nexus:
        # Specifies the type of the primary artifact repository.
        type: "nexus"  # Optional but makes the configuration clearer.
  
        # The URL of the primary Nexus repository.
        url: "https://registry.bytehawks.org/repository"
  
        # The name of the repository within Nexus where artifacts are stored.
        repository: "packages"
  
        # Template for the path where artifacts are stored in the repository.
        # Example: "{package}/{major_minor}/{full_version}".
        path_template: "{package}/{major_minor}/{full_version}"
  
        # The username for authenticating with the Nexus repository.
        username: "${NEXUS_USER}"
  
        # The password for authenticating with the Nexus repository.
        password: "${NEXUS_PASSWORD}"
  
        # Timeout in seconds for operations with the Nexus repository.
        timeout: 60
  
        # Number of retry attempts for operations with the Nexus repository.
        retry: 3
  
    fallback:
      s3:
        # Specifies the type of the fallback artifact repository.
        type: "s3"  # Optional but makes the configuration clearer.
  
        # The name of the S3 bucket used as the fallback repository.
        bucket: "bytehawks-packages"
  
        # The AWS region where the S3 bucket is located.
        region: "eu-south-1"
  
        # The endpoint URL for the S3 bucket.
        # Optional but can be specified for custom endpoints.
        endpoint: "https://s3.eu-south-1.amazonaws.com"
  
        # Template for the path where artifacts are stored in the S3 bucket.
        # Example: "{package}/{major_minor}/{full_version}".
        path_template: "{package}/{major_minor}/{full_version}"
  
        # The access key for authenticating with the S3 bucket.
        access_key: "${AWS_ACCESS_KEY_ID}"
  
        # The secret key for authenticating with the S3 bucket.
        secret_key: "${AWS_SECRET_ACCESS_KEY}"
  
        # Timeout in seconds for operations with the S3 bucket.
        timeout: 60
  
        # Number of retry attempts for operations with the S3 bucket.
        retry: 3
  
  # Logging configuration for observability.
  logging:
    # The logging level to use (e.g., DEBUG, INFO, WARN, ERROR).
    level: "INFO"
  
    # The format of the log output (e.g., plain text, JSON).
    format: "json"
  
    # The output destination for logs (e.g., stdout, file path).
    output: "stdout"
  
  # GitHub integration configuration for upstream monitoring.
  github:
    # The GitHub token used for authentication with the GitHub API.
    token: "${GITHUB_TOKEN}"
  
    # The base URL for the GitHub API.
    api_url: "https://api.github.com"
```