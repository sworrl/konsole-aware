// konsole-aware loader: the small library Konsole preloads (libkonsole_aware_menu.so).
//
// All of the real work (right-click menu, tab fills, terminal tint, update tray icon)
// lives in libkonsole_aware_module.so. This loader loads that module, watches its
// file, and swaps in the new copy whenever an update replaces it, so updates reach
// Konsole windows that are already open. Nothing here changes from one release to the
// next, so the loader itself never needs Konsole restarted.
//
// The loader can also be loaded into a Konsole that is already running and was
// started before konsole-aware had a loader (`konsole-aware hotload` does that with
// gdb). It starts the module the same way in both cases.
//
// Where the module is found, first match wins:
//   $KONSOLE_AWARE_MODULE
//   ~/.local/lib/konsole-aware/libkonsole_aware_module.so      (source installs)
//   /usr/lib/konsole-aware/menu_hook/libkonsole_aware_module.so  (.deb and .rpm)
//
// Each load copies the module to a new file name first. dlopen hands back the library
// it already has when the path matches, and the old copy stays mapped (never
// dlclose'd) so nothing still pointing into it can crash.

#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif

#include <dlfcn.h>
#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include <string>

#include <QCoreApplication>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QFileSystemWatcher>
#include <QMetaObject>
#include <QTimer>

namespace {

constexpr const char kLibBasename[] = "libkonsole_aware_menu.so";
constexpr int kModuleAbi = 1; // bump when ka_module_* change shape

bool s_isKonsoleProcess = false;

// Keep LD_PRELOAD from reaching every shell and tool Konsole starts.
void stripSelfFromLdPreload()
{
    const char *cur = getenv("LD_PRELOAD");
    if (!cur) {
        return;
    }
    const std::string in(cur);
    std::string out;
    bool removed = false;
    size_t pos = 0;
    while (pos <= in.size()) { // ld.so accepts ':' and whitespace as separators
        size_t end = in.find_first_of(": \t", pos);
        if (end == std::string::npos) {
            end = in.size();
        }
        const std::string tok = in.substr(pos, end - pos);
        if (!tok.empty()) {
            const size_t slash = tok.rfind('/');
            const std::string base = slash == std::string::npos ? tok : tok.substr(slash + 1);
            if (base == kLibBasename) {
                removed = true;
            } else {
                if (!out.empty()) {
                    out += ':';
                }
                out += tok;
            }
        }
        pos = end + 1;
    }
    if (!removed) {
        return;
    }
    if (out.empty()) {
        unsetenv("LD_PRELOAD");
    } else {
        setenv("LD_PRELOAD", out.c_str(), 1);
    }
}

__attribute__((constructor)) void loaderInit()
{
    stripSelfFromLdPreload();
    s_isKonsoleProcess = program_invocation_short_name && strcmp(program_invocation_short_name, "konsole") == 0;
}

QString modulePath()
{
    const QString fromEnv = qEnvironmentVariable("KONSOLE_AWARE_MODULE");
    if (!fromEnv.isEmpty()) {
        return fromEnv;
    }
    const QString local = QDir::homePath() + QStringLiteral("/.local/lib/konsole-aware/libkonsole_aware_module.so");
    if (QFileInfo::exists(local)) {
        return local;
    }
    return QStringLiteral("/usr/lib/konsole-aware/menu_hook/libkonsole_aware_module.so");
}

QString copiesDir()
{
    QString dir = qEnvironmentVariable("XDG_RUNTIME_DIR");
    if (dir.isEmpty()) {
        dir = QDir::tempPath();
    }
    return dir + QStringLiteral("/konsole-aware/modules");
}

struct Module {
    void (*stop)() = nullptr;
    dev_t dev = 0;
    ino_t inode = 0;
    qint64 mtime = 0;
    qint64 size = 0;
};
Module s_module;
int s_loads = 0;

// Load the module if its file differs from the one running. The running module keeps
// going when the new one can't be loaded.
void loadModule()
{
    const QString source = modulePath();
    struct stat st;
    if (stat(QFile::encodeName(source).constData(), &st) != 0) {
        if (s_module.stop) { // uninstalled: take konsole-aware out of this Konsole too
            s_module.stop();
            s_module = Module();
        }
        return;
    }
    if (s_module.stop && st.st_dev == s_module.dev && st.st_ino == s_module.inode && st.st_mtime == s_module.mtime
        && st.st_size == s_module.size) {
        return;
    }
    QDir().mkpath(copiesDir());
    QFile::setPermissions(copiesDir(), QFileDevice::ReadOwner | QFileDevice::WriteOwner | QFileDevice::ExeOwner);
    const QString copy = copiesDir() + QStringLiteral("/module-%1-%2.so").arg(getpid()).arg(++s_loads);
    QFile::remove(copy);
    if (!QFile::copy(source, copy)) {
        qWarning("konsole-aware: could not copy %s", qPrintable(source));
        return;
    }
    void *handle = dlopen(QFile::encodeName(copy).constData(), RTLD_NOW | RTLD_LOCAL);
    if (qEnvironmentVariableIsEmpty("KONSOLE_AWARE_DEBUG")) {
        QFile::remove(copy); // stays mapped; nothing left behind on disk
    }
    if (!handle) {
        qWarning("konsole-aware: could not load %s: %s", qPrintable(source), dlerror());
        return;
    }
    auto abi = reinterpret_cast<int (*)()>(dlsym(handle, "ka_module_abi"));
    auto start = reinterpret_cast<void (*)()>(dlsym(handle, "ka_module_start"));
    auto stop = reinterpret_cast<void (*)()>(dlsym(handle, "ka_module_stop"));
    if (!abi || !start || !stop || abi() != kModuleAbi) {
        qWarning("konsole-aware: %s doesn't fit this loader; restart Konsole to pick it up", qPrintable(source));
        return;
    }
    if (s_module.stop) {
        s_module.stop(); // the old copy takes down everything it set up
    }
    start();
    s_module = {stop, st.st_dev, st.st_ino, static_cast<qint64>(st.st_mtime), static_cast<qint64>(st.st_size)};
}

void watchModule()
{
    // The installer and package managers replace the file with a rename, which shows up
    // as a change to its folder.
    const QString dir = QFileInfo(modulePath()).absolutePath();
    QDir().mkpath(dir);
    auto *watcher = new QFileSystemWatcher(QStringList{dir}, qApp);
    auto *settle = new QTimer(qApp);
    settle->setSingleShot(true);
    settle->setInterval(500);
    QObject::connect(settle, &QTimer::timeout, qApp, loadModule);
    QObject::connect(watcher, &QFileSystemWatcher::directoryChanged, settle, qOverload<>(&QTimer::start));
}

void loaderStart()
{
    static bool started = false;
    const bool konsole = s_isKonsoleProcess || QCoreApplication::applicationName() == QLatin1String("konsole");
    if (started || !konsole || !qEnvironmentVariableIsEmpty("KONSOLE_AWARE_NO_MODULE")) {
        return;
    }
    started = true;
    // Queued: this runs inside QCoreApplication's constructor when preloaded, and inside
    // dlopen when loaded into a running Konsole. Neither is a place to touch widgets.
    QMetaObject::invokeMethod(
        QCoreApplication::instance(),
        []() {
            loadModule();
            watchModule();
        },
        Qt::QueuedConnection);
}

} // namespace

// Runs from QCoreApplication's constructor when preloaded, or right away when this
// library is loaded into a Konsole that is already running.
Q_COREAPP_STARTUP_FUNCTION(loaderStart)

// For `konsole-aware hotload`: shows in /proc/<pid>/maps-based checks and lets a
// debugger confirm the loader is in.
extern "C" __attribute__((visibility("default"))) int konsole_aware_loader_abi()
{
    return kModuleAbi;
}
