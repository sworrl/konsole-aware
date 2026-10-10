Name:           konsole-aware
Version:        %{ka_version}
Release:        1%{?dist}
Summary:        Konsole tab awareness for AI coding agents and shell tasks
License:        GPL-3.0-or-later
URL:            https://github.com/sworrl/konsole-aware
Source0:        %{name}-%{version}.tar.gz

BuildRequires:  gcc-c++, make, qt6-qtbase-devel, pkgconf-pkg-config
Requires:       python3 >= 3.8, konsole, qt6-qtbase-gui
Recommends:     python3-gobject, libnotify, pulseaudio-utils, gdb

%global debug_package %{nil}
%global __brp_mangle_shebangs %{nil}

%description
Tab titles, colors and badges in KDE Konsole that show which AI coding agent
(Claude Code, Antigravity, Gemini, Codex, ...) runs in each tab, its model,
effort and mode, and whether it is working, done, or waiting for input. Also
tracks long-running shell commands and snaps same-project tabs together.

After installing, each user runs: konsole-aware install

%prep
%setup -q

%build
make -C menu_hook

%install
packaging/stage.sh %{buildroot}

%post
echo "konsole-aware installed. Each user enables it with: konsole-aware install"

%files
%license LICENSE
/usr/lib/konsole-aware
/usr/bin/konsole-aware
/usr/share/doc/konsole-aware

%changelog
* Sat Oct 10 2026 sworrl <github@falcontechnix.com> - 1.4.0-1
- See CHANGELOG.md

* Fri Oct 09 2026 sworrl <github@falcontechnix.com> - 1.3.0-1
- See CHANGELOG.md

* Fri Oct 09 2026 sworrl <github@falcontechnix.com> - 1.2.0-1
- See CHANGELOG.md

* Thu Oct 01 2026 sworrl <github@falcontechnix.com> - 1.1.0-1
- See CHANGELOG.md

