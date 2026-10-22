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

      # Where build steps run.
      #   "container": the CLI spawns the builder image with Docker/Podman (local development).
      #   "native":    the CLI already runs inside the builder image (CI job container on K3s).
      executor: "container"

      # Parallel jobs passed to make/cmake as $JOBS ("auto" = number of CPUs).
      jobs: "auto"

      # Toolchain flags exported to every recipe step. -fPIC lets static libraries be
      # linked into both static executables and the shared modules of relocatable packages.
      env:
        CFLAGS: "-O2 -pipe -fPIC -fstack-protector-strong -D_FORTIFY_SOURCE=2 -ffile-prefix-map=/opt/bytehawks=."
        CXXFLAGS: "-O2 -pipe -fPIC -fstack-protector-strong -D_FORTIFY_SOURCE=2 -ffile-prefix-map=/opt/bytehawks=."
        LDFLAGS: "-static -Wl,-z,relro,-z,now"

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
        image_basename: "distillery-builder"
  
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

  # Artifact repositories for compiled packages.
  repository:
    # How artifacts are published:
    #   "primary-only"          only the primary endpoint(s)
    #   "primary-with-fallback" first endpoint that succeeds (fallback used on failure)
    #   "mirror-all"            every endpoint must receive the artifact (Nexus AND GitHub)
    # Downloads (dependency fetch) always try endpoints in order: primary, then fallback.
    strategy: "mirror-all"

    primary:
      nexus:
        type: "nexus"

        # Nexus base URL, including the /repository suffix. ${VAR:-default} is supported.
        url: "${NEXUS_URL:-http://nexus.nexus.svc.cluster.local:8081/repository}"

        # Nexus "raw (hosted)" repository. Disable redeploy to make artifacts immutable.
        repository: "bytehawks-packages"

        # Directory layout inside the repository; the file name is
        # <package>-<version>-<variant>-linux-<arch>.tar.gz (+ .sha256, .manifest.json, .sbom.cdx.json).
        path_template: "{package}/{major_minor}/{full_version}"

        username: "${NEXUS_USER}"
        password: "${NEXUS_PASSWORD}"

        # Set to false for self-signed certificates.
        verify_tls: true
        timeout: 60
        retry: 3

    fallback:
      github:
        type: "github"

        # Repository whose Releases hold the artifacts: one release per package version
        # (tag from tag_template), one asset per file, all variants in the same release.
        repo: "bytehawks-org/artifacts"
        tag_template: "{package}-{full_version}"

        # Token with "contents: write" on the repository above (fine-grained PAT).
        token: "${ARTIFACTS_GITHUB_TOKEN}"
        timeout: 120
        retry: 3

      # type "s3" is still accepted by the schema but no uploader is implemented yet.

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