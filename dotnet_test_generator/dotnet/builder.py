"""Build operations for .NET solutions."""

import subprocess
import re
import os
import time
import base64
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from pathlib import Path

from dotnet_test_generator.core.exceptions import BuildError
from dotnet_test_generator.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class BuildErrorInfo:
    """Information about a build error."""

    file: str
    line: int
    column: int
    code: str
    message: str
    severity: str = "error"


@dataclass
class BuildResult:
    """Result of a build operation."""

    success: bool
    duration_seconds: float
    errors: list[BuildErrorInfo] = field(default_factory=list)
    warnings: list[BuildErrorInfo] = field(default_factory=list)
    output: str = ""

    @property
    def error_count(self) -> int:
        return len(self.errors)

    @property
    def warning_count(self) -> int:
        return len(self.warnings)


class SolutionBuilder:
    """
    Handles .NET build operations.

    Provides methods for restoring, building, and cleaning solutions.
    Uses detailed logging for full transparency.
    """

    def __init__(self, repo_path: Path):
        """
        Initialize builder.

        Args:
            repo_path: Path to repository root
        """
        self.repo_path = repo_path
        self._solution_file: str | None = None

    def _find_solution_file(self) -> str | None:
        """Find the solution file in the repo."""
        if self._solution_file:
            return self._solution_file

        logger.info("[DOTNET] Searching for solution file...")

        # Look for .sln files
        sln_files = list(self.repo_path.glob("*.sln"))

        if not sln_files:
            # Check one level deep
            sln_files = list(self.repo_path.glob("*/*.sln"))

        if sln_files:
            # Prefer the one in root, or first found
            self._solution_file = str(sln_files[0].relative_to(self.repo_path))
            logger.info(f"[DOTNET] Found solution: {self._solution_file}")
            return self._solution_file

        logger.warning("[DOTNET] No solution file found - will build all projects")
        return None

    def _log_command(self, cmd: list[str], description: str) -> None:
        """Log the command being executed."""
        logger.info(f"[DOTNET] {description}")
        logger.info(f"[DOTNET] Command: {' '.join(cmd)}")
        logger.info(f"[DOTNET] Working directory: {self.repo_path}")

    def _run_command_streaming(
        self,
        cmd: list[str],
        timeout: int,
        description: str,
    ) -> subprocess.CompletedProcess:
        """
        Run a dotnet command with real-time output streaming.
        Shows output as it happens instead of waiting for completion.
        """
        self._log_command(cmd, description)
        logger.info("[DOTNET] === BEGIN LIVE OUTPUT ===")
        start_time = time.time()

        stdout_lines = []
        stderr_lines = []

        try:
            # Use Popen for real-time streaming
            process = subprocess.Popen(
                cmd,
                cwd=self.repo_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,  # Line buffered
            )

            import selectors
            sel = selectors.DefaultSelector()
            sel.register(process.stdout, selectors.EVENT_READ)
            sel.register(process.stderr, selectors.EVENT_READ)

            last_output_time = time.time()

            while True:
                # Check timeout
                elapsed = time.time() - start_time
                if elapsed > timeout:
                    process.kill()
                    logger.error(f"[DOTNET] Command timed out after {timeout}s")
                    raise subprocess.TimeoutExpired(cmd, timeout)

                # Check for output with small timeout
                events = sel.select(timeout=5)

                if not events:
                    # No output for 5 seconds, log heartbeat
                    waiting_time = time.time() - last_output_time
                    if waiting_time > 10:
                        logger.info(f"[DOTNET] ... still waiting ({elapsed:.0f}s elapsed, no output for {waiting_time:.0f}s)")

                for key, _ in events:
                    line = key.fileobj.readline()
                    if line:
                        last_output_time = time.time()
                        line = line.rstrip()

                        if key.fileobj == process.stdout:
                            stdout_lines.append(line)
                        else:
                            stderr_lines.append(line)

                        # Log the line with appropriate level
                        line_lower = line.lower()
                        if 'error' in line_lower:
                            logger.error(f"[DOTNET] {line}")
                        elif 'warning' in line_lower:
                            logger.warning(f"[DOTNET] {line}")
                        else:
                            logger.info(f"[DOTNET] {line}")

                # Check if process has finished
                if process.poll() is not None:
                    # Read any remaining output
                    for line in process.stdout:
                        line = line.rstrip()
                        stdout_lines.append(line)
                        logger.info(f"[DOTNET] {line}")
                    for line in process.stderr:
                        line = line.rstrip()
                        stderr_lines.append(line)
                        logger.error(f"[DOTNET] {line}")
                    break

            sel.close()
            duration = time.time() - start_time
            logger.info("[DOTNET] === END LIVE OUTPUT ===")
            logger.info(f"[DOTNET] Completed in {duration:.1f}s with exit code {process.returncode}")

            # Return a CompletedProcess-like result
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=process.returncode,
                stdout='\n'.join(stdout_lines),
                stderr='\n'.join(stderr_lines),
            )

        except subprocess.TimeoutExpired:
            raise
        except Exception as e:
            logger.error(f"[DOTNET] Command failed: {e}")
            raise

    def _run_command(
        self,
        cmd: list[str],
        timeout: int,
        description: str,
        log_all_output: bool = False,
        stream_output: bool = False,
    ) -> subprocess.CompletedProcess:
        """
        Run a dotnet command with full logging.

        Args:
            cmd: Command to run
            timeout: Timeout in seconds
            description: Description for logging
            log_all_output: If True, log all output lines
            stream_output: If True, stream output in real-time

        Returns:
            CompletedProcess result
        """
        # Use streaming for long-running commands
        if stream_output:
            return self._run_command_streaming(cmd, timeout, description)

        self._log_command(cmd, description)
        start_time = time.time()

        try:
            result = subprocess.run(
                cmd,
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            duration = time.time() - start_time
            logger.info(f"[DOTNET] Completed in {duration:.1f}s with exit code {result.returncode}")

            # Log output
            combined_output = (result.stdout or "") + (result.stderr or "")

            if log_all_output and combined_output:
                logger.info("[DOTNET] === BEGIN OUTPUT ===")
                for line in combined_output.split('\n'):
                    if line.strip():
                        # Color code by type
                        line_lower = line.lower()
                        if 'error' in line_lower:
                            logger.error(f"[DOTNET] {line}")
                        elif 'warning' in line_lower:
                            logger.warning(f"[DOTNET] {line}")
                        else:
                            logger.info(f"[DOTNET] {line}")
                logger.info("[DOTNET] === END OUTPUT ===")
            else:
                # Log key lines only
                for line in combined_output.split('\n'):
                    line = line.strip()
                    if not line:
                        continue
                    line_lower = line.lower()
                    if 'error' in line_lower:
                        logger.error(f"[DOTNET] {line}")
                    elif 'warning' in line_lower:
                        logger.warning(f"[DOTNET] {line}")
                    elif any(kw in line_lower for kw in ['restoring', 'restored', 'build succeeded', 'build failed', 'failed', 'passed']):
                        logger.info(f"[DOTNET] {line}")

            return result

        except subprocess.TimeoutExpired:
            logger.error(f"[DOTNET] Command timed out after {timeout}s")
            raise
        except Exception as e:
            logger.error(f"[DOTNET] Command failed: {e}")
            raise

    def _ensure_global_nuget_config(self, feed_url: str, pat: str) -> None:
        """Ensure global NuGet.Config exists with authenticated internal feed.

        Creates/updates ~/.nuget/NuGet/NuGet.Config so dotnet restore can
        authenticate with the private Azure DevOps feed regardless of
        repo-level config files.
        """
        import xml.etree.ElementTree as ET

        global_config_dir = Path.home() / ".nuget" / "NuGet"
        global_config_path = global_config_dir / "NuGet.Config"

        logger.info(f"[NUGET] Ensuring global NuGet.Config at {global_config_path}")

        # Create directory if needed
        global_config_dir.mkdir(parents=True, exist_ok=True)

        # Parse existing or create new
        if global_config_path.exists():
            try:
                tree = ET.parse(global_config_path)
                root = tree.getroot()
            except ET.ParseError:
                logger.warning("[NUGET] Failed to parse existing global config, recreating")
                root = ET.Element("configuration")
                tree = ET.ElementTree(root)
        else:
            root = ET.Element("configuration")
            tree = ET.ElementTree(root)

        # Ensure packageSources section
        sources = root.find("packageSources")
        if sources is None:
            sources = ET.SubElement(root, "packageSources")

        # Check existing sources
        has_nuget_org = False
        has_internal = False
        for source in sources.findall("add"):
            if source.get("key") == "nuget.org":
                has_nuget_org = True
            if source.get("key") == "InternalFeed":
                has_internal = True
                source.set("value", feed_url)

        if not has_nuget_org:
            nuget_source = ET.SubElement(sources, "add")
            nuget_source.set("key", "nuget.org")
            nuget_source.set("value", "https://api.nuget.org/v3/index.json")

        if not has_internal:
            internal_source = ET.SubElement(sources, "add")
            internal_source.set("key", "InternalFeed")
            internal_source.set("value", feed_url)

        # Ensure packageSourceCredentials section
        creds = root.find("packageSourceCredentials")
        if creds is None:
            creds = ET.SubElement(root, "packageSourceCredentials")

        # Replace existing InternalFeed credentials
        existing_creds = creds.find("InternalFeed")
        if existing_creds is not None:
            creds.remove(existing_creds)

        internal_creds = ET.SubElement(creds, "InternalFeed")
        username_elem = ET.SubElement(internal_creds, "add")
        username_elem.set("key", "Username")
        username_elem.set("value", "az")
        password_elem = ET.SubElement(internal_creds, "add")
        password_elem.set("key", "ClearTextPassword")
        password_elem.set("value", pat)

        # Write the config
        tree.write(global_config_path, encoding="utf-8", xml_declaration=True)
        logger.info(f"[NUGET] ✓ Global NuGet.Config written with authenticated InternalFeed")

        # Verify by listing sources
        list_result = subprocess.run(
            ["dotnet", "nuget", "list", "source"],
            capture_output=True,
            text=True,
        )
        logger.info(f"[NUGET] Global NuGet sources:\n{list_result.stdout}")

    def _test_network_connectivity(self) -> None:
        """
        Test network connectivity to NuGet feeds before restore.
        Logs detailed diagnostics for troubleshooting.
        """
        logger.info("=" * 60)
        logger.info("[NETWORK] PRE-RESTORE CONNECTIVITY DIAGNOSTICS")
        logger.info("=" * 60)

        # Log proxy settings
        http_proxy = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
        https_proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        no_proxy = os.environ.get("NO_PROXY") or os.environ.get("no_proxy")

        logger.info(f"[NETWORK] HTTP_PROXY: {http_proxy or '(not set)'}")
        logger.info(f"[NETWORK] HTTPS_PROXY: {https_proxy or '(not set)'}")
        logger.info(f"[NETWORK] NO_PROXY: {no_proxy or '(not set)'}")
        logger.info("-" * 40)

        # Get PAT for internal feed auth
        pat = os.environ.get("AZURE_DEVOPS_PERSONAL_ACCESS_TOKEN", "")

        # Test endpoints
        endpoints = [
            ("nuget.org (API)", "https://api.nuget.org/v3/index.json", None),
            ("nuget.org (packages)", "https://www.nuget.org", None),
        ]

        # Add internal feed if configured (with auth)
        internal_feed = os.environ.get("NUGET_INTERNAL_FEED", "")
        if internal_feed:
            endpoints.append(("Internal Feed", internal_feed, pat))

        for name, url, auth_pat in endpoints:
            self._test_endpoint(name, url, auth_pat=auth_pat)

        logger.info("=" * 60)

    def _test_endpoint(self, name: str, url: str, timeout: int = 30, auth_pat: str | None = None) -> bool:
        """Test connectivity to a single endpoint."""
        logger.info(f"[NETWORK] Testing: {name}")
        logger.info(f"[NETWORK]   URL: {url}")
        if auth_pat:
            logger.info(f"[NETWORK]   Auth: PAT (Basic Auth)")

        try:
            # Create request
            req = urllib.request.Request(url, method='GET')
            req.add_header('User-Agent', 'NuGet-Diagnostics/1.0')

            # Add Basic Auth if PAT provided (Azure DevOps style)
            if auth_pat:
                # Azure DevOps uses empty username with PAT as password
                credentials = f":{auth_pat}"
                encoded = base64.b64encode(credentials.encode('utf-8')).decode('utf-8')
                req.add_header('Authorization', f'Basic {encoded}')

            start_time = time.time()
            with urllib.request.urlopen(req, timeout=timeout) as response:
                elapsed = time.time() - start_time
                status = response.status
                logger.info(f"[NETWORK]   ✓ SUCCESS - Status: {status}, Time: {elapsed:.2f}s")
                return True

        except urllib.error.HTTPError as e:
            elapsed = time.time() - start_time
            logger.warning(f"[NETWORK]   ⚠ HTTP Error: {e.code} {e.reason} (Time: {elapsed:.2f}s)")
            if e.code == 401:
                logger.error(f"[NETWORK]   → 401 Unauthorized - PAT may be invalid or expired")
            elif e.code == 403:
                logger.error(f"[NETWORK]   → 403 Forbidden - PAT may lack permissions")
            return False

        except urllib.error.URLError as e:
            logger.error(f"[NETWORK]   ✗ FAILED - URL Error: {e.reason}")
            if "proxy" in str(e.reason).lower():
                logger.error(f"[NETWORK]   → Proxy issue detected! Check HTTP_PROXY/HTTPS_PROXY and NO_PROXY settings")
            elif "timeout" in str(e.reason).lower():
                logger.error(f"[NETWORK]   → Connection timed out after {timeout}s")
            elif "connection refused" in str(e.reason).lower():
                logger.error(f"[NETWORK]   → Connection refused - is the server reachable?")
            return False

        except Exception as e:
            logger.error(f"[NETWORK]   ✗ FAILED - {type(e).__name__}: {e}")
            return False

    def restore(
        self,
        project: str | None = None,
        timeout: int = 600,
    ) -> bool:
        """
        Restore NuGet packages.

        Args:
            project: Optional project/solution path
            timeout: Timeout in seconds

        Returns:
            True if restore succeeded
        """
        # Run network diagnostics first
        self._test_network_connectivity()

        logger.info("=" * 60)
        logger.info("[DOTNET] STEP: PACKAGE RESTORE")
        logger.info("=" * 60)

        # Check for internal NuGet feed configuration
        nuget_feed_url = os.environ.get("NUGET_INTERNAL_FEED", "")
        nuget_feed_pat = os.environ.get("AZURE_DEVOPS_PERSONAL_ACCESS_TOKEN", "")

        # PRIMARY: Create global NuGet.Config with credentials
        # This ensures ~/.nuget/NuGet/NuGet.Config has the authenticated feed
        # so dotnet restore works regardless of repo-level config state
        if nuget_feed_url and nuget_feed_pat:
            self._ensure_global_nuget_config(nuget_feed_url, nuget_feed_pat)

        # SECONDARY: Set VSS_NUGET_EXTERNAL_FEED_ENDPOINTS for Azure Artifacts
        # Credential Provider (if installed). Belt-and-suspenders approach.
        if nuget_feed_url and nuget_feed_pat:
            import json
            endpoints = {
                "endpointCredentials": [
                    {
                        "endpoint": nuget_feed_url,
                        "username": "az",
                        "password": nuget_feed_pat
                    }
                ]
            }
            os.environ["VSS_NUGET_EXTERNAL_FEED_ENDPOINTS"] = json.dumps(endpoints)
            logger.info(f"[NUGET] Set VSS_NUGET_EXTERNAL_FEED_ENDPOINTS for Azure Artifacts auth")

        # TERTIARY: Also configure repo-level NuGet sources as fallback
        if nuget_feed_url:
            logger.info(f"[DOTNET] Internal NuGet feed configured")
            self._configure_nuget_source(nuget_feed_url, nuget_feed_pat)

        # Find solution file if no project specified
        if not project:
            project = self._find_solution_file()

        # Build command with detailed verbosity
        cmd = ["dotnet", "restore", "--verbosity", "detailed", "--interactive", "false"]
        if project:
            cmd.append(project)

        try:
            result = self._run_command(
                cmd,
                timeout=timeout,
                description=f"Restoring packages for {project or 'all projects'}",
                stream_output=True,  # Stream output in real-time to see progress
            )

            if result.returncode == 0:
                logger.info("[DOTNET] ✓ Restore completed successfully")
                return True
            else:
                logger.error("[DOTNET] ✗ Restore FAILED")
                return False

        except subprocess.TimeoutExpired:
            logger.error(f"[DOTNET] ✗ Restore timed out after {timeout}s")
            return False
        except Exception as e:
            logger.error(f"[DOTNET] ✗ Restore failed: {e}")
            return False

    def _configure_nuget_source(self, feed_url: str, pat: str) -> None:
        """Configure NuGet to use an internal feed with authentication.

        This method:
        1. Finds existing NuGet.config in the repo
        2. Adds credentials directly to it (repo config takes precedence)
        3. Also adds as global source as fallback
        """
        import xml.etree.ElementTree as ET

        logger.info(f"[NUGET] Configuring authenticated NuGet source")
        logger.info(f"[NUGET]   URL: {feed_url}")
        logger.info(f"[NUGET]   Auth: {'PAT provided' if pat else 'No PAT!'}")

        # Find NuGet.config files in repo
        nuget_configs = list(self.repo_path.glob("**/[Nn]u[Gg]et.[Cc]onfig"))
        logger.info(f"[NUGET] Found {len(nuget_configs)} NuGet.config files in repo")

        for config_path in nuget_configs:
            logger.info(f"[NUGET]   - {config_path.relative_to(self.repo_path)}")

        # Update each NuGet.config to add credentials
        for config_path in nuget_configs:
            self._add_credentials_to_nuget_config(config_path, feed_url, pat)

        # If no NuGet.config found, create one in repo root
        if not nuget_configs:
            self._create_nuget_config_with_credentials(feed_url, pat)

        # Also add as global source (fallback)
        logger.info(f"[NUGET] Adding global source as fallback...")
        source_name = "InternalFeed"
        subprocess.run(
            ["dotnet", "nuget", "remove", "source", source_name],
            cwd=self.repo_path,
            capture_output=True,
            text=True,
        )
        cmd = ["dotnet", "nuget", "add", "source", feed_url, "--name", source_name]
        if pat:
            cmd.extend(["--username", "az", "--password", pat, "--store-password-in-clear-text"])
        subprocess.run(cmd, cwd=self.repo_path, capture_output=True, text=True)

        # List sources to confirm
        list_result = subprocess.run(
            ["dotnet", "nuget", "list", "source"],
            cwd=self.repo_path,
            capture_output=True,
            text=True,
        )
        logger.info(f"[NUGET] Final sources:\n{list_result.stdout}")

    def _add_credentials_to_nuget_config(self, config_path: Path, feed_url: str, pat: str) -> None:
        """Add credentials to an existing NuGet.config file."""
        import xml.etree.ElementTree as ET

        logger.info(f"[NUGET] Updating {config_path.name} with credentials...")

        try:
            tree = ET.parse(config_path)
            root = tree.getroot()

            # Find all package sources to get their keys
            sources = {}
            package_sources = root.find("packageSources")
            if package_sources is not None:
                for source in package_sources.findall("add"):
                    key = source.get("key", "")
                    value = source.get("value", "")
                    sources[key] = value
                    logger.info(f"[NUGET]   Found source: {key} -> {value[:50]}...")

            # Find or create packageSourceCredentials section
            creds_section = root.find("packageSourceCredentials")
            if creds_section is None:
                creds_section = ET.SubElement(root, "packageSourceCredentials")
                logger.info(f"[NUGET]   Created packageSourceCredentials section")

            # Add credentials for sources that match our internal feed URL
            creds_added = False
            for key, value in sources.items():
                # Check if this source matches our internal feed (by domain)
                if "devops.malaffi.ae" in value or "_packaging" in value:
                    # Create safe element name (replace special chars)
                    safe_key = key.replace(" ", "_").replace("-", "_")

                    # Remove existing credentials for this source
                    existing = creds_section.find(safe_key)
                    if existing is not None:
                        creds_section.remove(existing)

                    # Add new credentials
                    source_creds = ET.SubElement(creds_section, safe_key)
                    username_elem = ET.SubElement(source_creds, "add")
                    username_elem.set("key", "Username")
                    username_elem.set("value", "az")
                    password_elem = ET.SubElement(source_creds, "add")
                    password_elem.set("key", "ClearTextPassword")
                    password_elem.set("value", pat)

                    logger.info(f"[NUGET]   ✓ Added credentials for source: {key}")
                    creds_added = True

            if creds_added:
                # Write back the updated config
                tree.write(config_path, encoding="utf-8", xml_declaration=True)
                logger.info(f"[NUGET]   ✓ Saved updated {config_path.name}")
            else:
                logger.warning(f"[NUGET]   No matching internal sources found in {config_path.name}")

        except Exception as e:
            logger.error(f"[NUGET]   ✗ Failed to update {config_path.name}: {e}")

    def _create_nuget_config_with_credentials(self, feed_url: str, pat: str) -> None:
        """Create a NuGet.config file with credentials in repo root."""
        import xml.etree.ElementTree as ET

        config_path = self.repo_path / "NuGet.config"
        logger.info(f"[NUGET] Creating NuGet.config with credentials at repo root")

        # Create the XML structure
        root = ET.Element("configuration")

        # Package sources
        sources = ET.SubElement(root, "packageSources")
        nuget_source = ET.SubElement(sources, "add")
        nuget_source.set("key", "nuget.org")
        nuget_source.set("value", "https://api.nuget.org/v3/index.json")
        internal_source = ET.SubElement(sources, "add")
        internal_source.set("key", "InternalFeed")
        internal_source.set("value", feed_url)

        # Credentials
        creds = ET.SubElement(root, "packageSourceCredentials")
        internal_creds = ET.SubElement(creds, "InternalFeed")
        username = ET.SubElement(internal_creds, "add")
        username.set("key", "Username")
        username.set("value", "az")
        password = ET.SubElement(internal_creds, "add")
        password.set("key", "ClearTextPassword")
        password.set("value", pat)

        # Write the file
        tree = ET.ElementTree(root)
        tree.write(config_path, encoding="utf-8", xml_declaration=True)
        logger.info(f"[NUGET] ✓ Created {config_path.name}")

    def build(
        self,
        project: str | None = None,
        configuration: str = "Debug",
        no_restore: bool = True,
        timeout: int = 300,
    ) -> BuildResult:
        """
        Build the solution or project.

        Args:
            project: Optional project/solution path
            configuration: Build configuration
            no_restore: Skip restore step
            timeout: Timeout in seconds

        Returns:
            BuildResult with outcome details
        """
        logger.info("=" * 60)
        logger.info("[DOTNET] STEP: BUILD")
        logger.info("=" * 60)

        # Find solution file if no project specified
        if not project:
            project = self._find_solution_file()

        start_time = time.time()

        # Build command with detailed verbosity for debugging
        cmd = [
            "dotnet", "build",
            "-c", configuration,
            "--verbosity", "detailed",  # Full MSBuild output
        ]

        if no_restore:
            cmd.append("--no-restore")
        if project:
            cmd.append(project)

        logger.info(f"[DOTNET] Configuration: {configuration}")
        logger.info(f"[DOTNET] No restore: {no_restore}")
        logger.info(f"[DOTNET] Project/Solution: {project or 'all'}")
        logger.info(f"[DOTNET] Timeout: {timeout}s")

        try:
            result = self._run_command(
                cmd,
                timeout=timeout,
                description=f"Building {project or 'all projects'}",
                stream_output=True,  # Stream output in real-time
            )

            duration = time.time() - start_time
            output = (result.stdout or "") + (result.stderr or "")

            # Parse errors and warnings
            errors, warnings = self._parse_build_output(output)

            success = result.returncode == 0

            logger.info("-" * 40)
            if success:
                logger.info(f"[DOTNET] ✓ Build SUCCEEDED in {duration:.1f}s")
            else:
                logger.error(f"[DOTNET] ✗ Build FAILED in {duration:.1f}s")

            logger.info(f"[DOTNET] Errors: {len(errors)}, Warnings: {len(warnings)}")

            if errors:
                logger.error("[DOTNET] Build errors:")
                for i, err in enumerate(errors, 1):
                    logger.error(f"[DOTNET]   {i}. {err.file}:{err.line}:{err.column}")
                    logger.error(f"[DOTNET]      {err.code}: {err.message}")

            return BuildResult(
                success=success,
                duration_seconds=duration,
                errors=errors,
                warnings=warnings,
                output=output,
            )

        except subprocess.TimeoutExpired:
            logger.error(f"[DOTNET] ✗ Build timed out after {timeout}s")
            return BuildResult(
                success=False,
                duration_seconds=timeout,
                errors=[BuildErrorInfo(
                    file="",
                    line=0,
                    column=0,
                    code="TIMEOUT",
                    message=f"Build timed out after {timeout} seconds",
                )],
                output="Build timed out",
            )
        except Exception as e:
            logger.error(f"[DOTNET] ✗ Build exception: {e}")
            return BuildResult(
                success=False,
                duration_seconds=0,
                errors=[BuildErrorInfo(
                    file="",
                    line=0,
                    column=0,
                    code="EXCEPTION",
                    message=str(e),
                )],
                output=str(e),
            )

    def _parse_build_output(
        self,
        output: str,
    ) -> tuple[list[BuildErrorInfo], list[BuildErrorInfo]]:
        """
        Parse MSBuild output for errors and warnings.

        Handles multiple error formats:
        - Standard: file(line,col): error CODE: message
        - MSBuild: MSBUILD : error CODE: message
        - Project: Project.csproj : error CODE: message
        """
        errors = []
        warnings = []
        seen = set()  # Deduplicate

        # Pattern 1: Standard file(line,col): error/warning CODE: message
        pattern1 = r"([^(]+)\((\d+),(\d+)\):\s*(error|warning)\s+(\w+):\s*(.+)"

        # Pattern 2: MSBUILD/Project level errors (no line number)
        pattern2 = r"^(.+?)\s*:\s*(error|warning)\s+(\w+):\s*(.+)$"

        for line in output.split("\n"):
            line = line.strip()
            if not line:
                continue

            # Try pattern 1 first (with line/column)
            match = re.search(pattern1, line)
            if match:
                key = (match.group(1), match.group(2), match.group(5))
                if key not in seen:
                    seen.add(key)
                    info = BuildErrorInfo(
                        file=match.group(1).strip(),
                        line=int(match.group(2)),
                        column=int(match.group(3)),
                        severity=match.group(4),
                        code=match.group(5),
                        message=match.group(6).strip(),
                    )
                    if info.severity == "error":
                        errors.append(info)
                    else:
                        warnings.append(info)
                continue

            # Try pattern 2 (no line number)
            match = re.search(pattern2, line)
            if match:
                key = (match.group(1), "0", match.group(3))
                if key not in seen:
                    seen.add(key)
                    info = BuildErrorInfo(
                        file=match.group(1).strip(),
                        line=0,
                        column=0,
                        severity=match.group(2),
                        code=match.group(3),
                        message=match.group(4).strip(),
                    )
                    if info.severity == "error":
                        errors.append(info)
                    else:
                        warnings.append(info)

        return errors, warnings

    def clean(
        self,
        project: str | None = None,
        timeout: int = 120,
    ) -> bool:
        """
        Clean build outputs.

        Args:
            project: Optional project/solution path
            timeout: Timeout in seconds

        Returns:
            True if clean succeeded
        """
        logger.info("[DOTNET] Cleaning build outputs")

        if not project:
            project = self._find_solution_file()

        cmd = ["dotnet", "clean", "--verbosity", "normal"]
        if project:
            cmd.append(project)

        try:
            result = self._run_command(
                cmd,
                timeout=timeout,
                description="Cleaning build outputs",
            )
            return result.returncode == 0

        except Exception as e:
            logger.error(f"[DOTNET] Clean failed: {e}")
            return False

    def build_and_fix(
        self,
        fixer_callback,
        max_iterations: int = 5,
    ) -> BuildResult:
        """
        Build with automatic error fixing.

        Args:
            fixer_callback: Callback function(errors) that attempts to fix errors
            max_iterations: Maximum fix iterations

        Returns:
            Final BuildResult
        """
        logger.info("=" * 60)
        logger.info("[DOTNET] BUILD AND FIX LOOP")
        logger.info(f"[DOTNET] Max iterations: {max_iterations}")
        logger.info("=" * 60)

        for iteration in range(max_iterations):
            logger.info("-" * 40)
            logger.info(f"[DOTNET] Build attempt {iteration + 1}/{max_iterations}")
            logger.info("-" * 40)

            result = self.build()

            if result.success:
                logger.info(f"[DOTNET] ✓ Build succeeded on attempt {iteration + 1}")
                return result

            if not result.errors:
                logger.warning("[DOTNET] Build failed but no errors parsed - check output above")
                logger.warning("[DOTNET] Raw output tail:")
                if result.output:
                    for line in result.output.split('\n')[-20:]:
                        if line.strip():
                            logger.warning(f"[DOTNET]   {line}")
                return result

            logger.info(f"[DOTNET] Build failed with {result.error_count} errors")
            logger.info(f"[DOTNET] Attempting to fix errors...")

            # Convert errors for fixer
            errors_dict = [
                {
                    "file": e.file,
                    "line": e.line,
                    "column": e.column,
                    "code": e.code,
                    "message": e.message,
                }
                for e in result.errors
            ]

            fixed = fixer_callback(errors_dict)

            if not fixed:
                logger.warning("[DOTNET] Fixer could not fix errors - stopping")
                return result

            logger.info("[DOTNET] Fixer made changes, rebuilding...")

        logger.error(f"[DOTNET] ✗ Build failed after {max_iterations} iterations")
        return result
