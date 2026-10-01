"""Icon resolution and management for project folders, directories, and tabs."""

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

from konsole_aware.config import load_config
from konsole_aware.state.manager import StateManager

# Default folder name to icon mappings (matched case-insensitively)
DEFAULT_FOLDER_ICONS: Dict[str, str] = {
    # Frontend / UI / Web
    "frontend": "🎨",
    "front": "🎨",
    "client": "🎨",
    "ui": "🎨",
    "web": "🌐",
    "app": "📦",
    "views": "🎨",
    "pages": "📄",
    "components": "🧩",
    "styles": "💅",
    # Backend / API / Services
    "backend": "⚙️",
    "server": "⚙️",
    "api": "🔌",
    "services": "⚙️",
    "routes": "🔀",
    "controllers": "🎛️",
    "handlers": "🎛️",
    # Testing & Quality
    "tests": "🧪",
    "test": "🧪",
    "testing": "🧪",
    "spec": "🧪",
    "specs": "🧪",
    "e2e": "🧪",
    # Documentation
    "docs": "📚",
    "doc": "📚",
    "documentation": "📚",
    "wiki": "📖",
    # Scripts & Utilities
    "scripts": "📜",
    "bin": "📜",
    "tools": "🛠️",
    "utils": "🔧",
    # Infrastructure, Cloud & DevOps
    "infra": "☁️",
    "infrastructure": "☁️",
    "terraform": "☁️",
    "k8s": "☸️",
    "kubernetes": "☸️",
    "helm": "☸️",
    "ansible": "☁️",
    "deploy": "🚀",
    "ci": "🤖",
    "cd": "🚀",
    "github": "🐙",
    ".github": "🐙",
    # Containers
    "docker": "🐳",
    "compose": "🐳",
    # Mobile
    "mobile": "📱",
    "ios": "🍎",
    "android": "🤖",
    # Data & ML
    "data": "📊",
    "dataset": "📊",
    "datasets": "📊",
    "models": "🧠",
    "notebooks": "📓",
    # Core & Architecture
    "core": "💎",
    "kernel": "🧠",
    "engine": "⚡",
    "src": "📦",
    "pkg": "📦",
    "packages": "📦",
    "libs": "📦",
    "modules": "📦",
    # Database
    "database": "🗄️",
    "db": "🗄️",
    "migrations": "🗄️",
    "sql": "🗄️",
    # Security
    "auth": "🔒",
    "security": "🛡️",
    # Contracts / Web3
    "contracts": "📜",
    "solidity": "📜",
    # Firmware / Embedded
    "firmware": "🎛️",
    "hardware": "🔌",
    "embedded": "🎛️",
    # Shell / Config
    "shell": "📜",
    "config": "⚙️",
    "settings": "⚙️",
    "assets": "🖼️",
    "images": "🖼️",
    "static": "🖼️",
    "public": "🌐",
    # User folders
    "downloads": "📥",
    "documents": "📄",
    "desktop": "🖥️",
    "music": "🎵",
    "pictures": "🖼️",
    "photos": "🖼️",
    "videos": "🎬",
}

# Project root marker files / indicators to icons
DEFAULT_PROJECT_FILE_ICONS: Dict[str, str] = {
    "Cargo.toml": "🦀",        # Rust
    "pyproject.toml": "🐍",    # Python
    "setup.py": "🐍",          # Python
    "requirements.txt": "🐍",  # Python
    "Pipfile": "🐍",           # Python
    "package.json": "📦",      # Node / JavaScript
    "tsconfig.json": "🟦",     # TypeScript
    "go.mod": "🐹",            # Go
    "pom.xml": "☕",           # Java Maven
    "build.gradle": "☕",      # Java / Kotlin Gradle
    "build.gradle.kts": "☕",  # Kotlin Gradle
    "CMakeLists.txt": "🛠️",    # C / C++
    "Makefile": "🛠️",          # C / C++ / Build
    "meson.build": "🛠️",       # Meson
    "Gemfile": "💎",           # Ruby
    "composer.json": "🐘",     # PHP
    "pubspec.yaml": "🎯",      # Dart / Flutter
    "mix.exs": "💧",           # Elixir
    "Package.swift": "🐦",     # Swift
    "Dockerfile": "🐳",        # Docker
    "docker-compose.yml": "🐳",# Docker Compose
    "docker-compose.yaml": "🐳",# Docker Compose
    ".git": "🐙",              # Git Repository
}


class IconResolver:
    """Resolves and auto-picks icons based on session state, folders, and project markers."""

    def __init__(self):
        self.config = load_config()
        self.state_mgr = StateManager()

    def get_folder_icons(self) -> Dict[str, str]:
        """Return combined folder icon mapping from defaults and user config."""
        mapping = dict(DEFAULT_FOLDER_ICONS)
        user_icons = self.config.get("folder_icons", {})
        mapping.update(user_icons)
        return mapping

    def get_project_file_icons(self) -> Dict[str, str]:
        """Return combined project marker file icon mapping."""
        mapping = dict(DEFAULT_PROJECT_FILE_ICONS)
        user_icons = self.config.get("project_file_icons", {})
        mapping.update(user_icons)
        return mapping

    def find_project_root(self, path: Optional[Union[str, Path]] = None) -> Path:
        """Find the root project directory by searching upwards for .git or project markers."""
        try:
            cur = Path(path or ".").resolve()
        except Exception:
            return Path(".").resolve()

        markers = set(self.get_project_file_icons().keys()) | {".git", ".konsole-icon", ".konsole-aware.json"}

        # Never treat $HOME or anything above it as a project root: a stray
        # package.json or Makefile there would swallow every directory below.
        try:
            home = Path.home().resolve()
        except Exception:
            home = None
        candidates = []
        for parent in [cur] + list(cur.parents):
            if home is not None and (parent == home or parent in home.parents):
                break
            candidates.append(parent)

        # Search upwards for .git first as strongest indicator
        for parent in candidates:
            if (parent / ".git").exists():
                return parent

        # Then search upwards for project marker files. A directory that holds several
        # projects (e.g. ~/Documents/GitHub with a stray package.json) is a workspace
        # container, not a project: stop there and use the directory below it.
        for i, parent in enumerate(candidates):
            if any((parent / marker).exists() for marker in markers):
                if parent != cur and self._is_container(parent, markers):
                    return candidates[i - 1] if i > 0 else cur
                return parent

        return cur

    @staticmethod
    def _is_container(path: Path, markers: set, limit: int = 2) -> bool:
        """True if at least `limit` immediate subdirectories look like projects."""
        found = 0
        try:
            with os.scandir(path) as it:
                for entry in it:
                    if not entry.is_dir(follow_symlinks=False) or entry.name.startswith("."):
                        continue
                    sub = Path(entry.path)
                    if (sub / ".git").exists() or any((sub / m).exists() for m in markers):
                        found += 1
                        if found >= limit:
                            return True
        except OSError:
            return False
        return False

    def _read_icon_file(self, dir_path: Path) -> Optional[str]:
        """Check for explicit icon definition files in a directory."""
        candidates = [".konsole-icon", ".icon", ".project-icon"]
        for c in candidates:
            p = dir_path / c
            if p.is_file():
                try:
                    content = p.read_text(encoding="utf-8").strip()
                    if content:
                        return content.splitlines()[0].strip()
                except Exception:
                    pass

        cfg_json = dir_path / ".konsole-aware.json"
        if cfg_json.is_file():
            try:
                data = json.loads(cfg_json.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("icon"):
                    return str(data["icon"]).strip()
            except Exception:
                pass

        return None

    def resolve_with_reason(
        self,
        session_key: Optional[str] = None,
        cwd: Optional[Union[str, Path]] = None,
        explicit_icon: Optional[str] = None,
    ) -> Tuple[str, str]:
        """
        Resolve icon and return tuple of (icon, reason).
        Reasons: explicit, session, file_override, subfolder, project_marker, default, none
        """
        if explicit_icon and explicit_icon.strip():
            return explicit_icon.strip(), "explicit"

        if session_key:
            sess = self.state_mgr.get_session(session_key)
            if sess and sess.get("icon"):
                return str(sess["icon"]).strip(), "session"

        try:
            cur_path = Path(cwd or ".").resolve()
        except Exception:
            cur_path = Path(".").resolve()

        # 1. Custom icon file in current folder
        icon_file = self._read_icon_file(cur_path)
        if icon_file:
            return icon_file, f"file_override({cur_path.name})"

        # 2. Check subfolder within project directory
        project_root = self.find_project_root(cur_path)
        folder_icons = self.get_folder_icons()

        if cur_path != project_root:
            try:
                rel = cur_path.relative_to(project_root)
                # Check from deepest folder component up to root
                for part in reversed(rel.parts):
                    part_lower = part.lower()
                    if part_lower in folder_icons:
                        return folder_icons[part_lower], f"subfolder({part})"
            except ValueError:
                # cur_path is not inside project_root (e.g. user home directory)
                pass

        # 3. Check current folder name itself (even if outside project root)
        cur_name_lower = cur_path.name.lower()
        if cur_name_lower in folder_icons:
            return folder_icons[cur_name_lower], f"folder({cur_path.name})"

        # 4. Check project root icon file
        if project_root != cur_path:
            root_icon_file = self._read_icon_file(project_root)
            if root_icon_file:
                return root_icon_file, f"file_override({project_root.name})"

        # 5. Check project root marker files (e.g. Cargo.toml, pyproject.toml, package.json)
        marker_icons = self.get_project_file_icons()
        for marker_file, icon in marker_icons.items():
            if (project_root / marker_file).exists():
                return icon, f"project_marker({marker_file})"

        # 6. Fallback default
        default_icon = self.config.get("default_icon", "📁")
        if default_icon:
            return default_icon, "default"

        return "", "none"

    def resolve(
        self,
        session_key: Optional[str] = None,
        cwd: Optional[Union[str, Path]] = None,
        explicit_icon: Optional[str] = None,
    ) -> str:
        """Resolve the appropriate icon for a given session, folder, and project."""
        icon, _ = self.resolve_with_reason(session_key=session_key, cwd=cwd, explicit_icon=explicit_icon)
        return icon
