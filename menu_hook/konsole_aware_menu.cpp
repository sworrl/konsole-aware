// konsole-aware: LD_PRELOAD hook that extends Konsole's tab-bar context menu.
//
// It interposes QMenu::exec(const QPoint&, QAction*). When the menu being shown
// is the tab context menu of a Konsole::TabbedViewContainer, extra actions are
// appended. Most actions shell out to the `konsole-aware` CLI with an explicit
// `--tab <sessionId> --service <konsole D-Bus service>` so they target the tab
// that was right-clicked (which Konsole does NOT make current) in this Konsole
// process (the CLI is launched from Konsole's main process, which has no
// KONSOLE_DBUS_SESSION in its environment).
//
// Konsole internals relied upon (verified against Konsole 26.08 sources):
//  - TabbedViewContainer creates the menu as `new QMenu(tabBar())` and calls
//    exec(tabBar()->mapToGlobal(point)); so menu->parentWidget() is the
//    DetachableTabBar and its parentWidget() is the TabbedViewContainer.
//  - Built-in actions carry objectNames "tab-detach", "edit-rename", "tab-close".
//  - TabbedViewContainer::detachTab(int) is a Qt signal (invokable by name).
//  - TabbedViewContainer::moveTabToWindow(int, QWidget*) is a plain exported
//    member function (resolved with dlsym).
//  - ViewManager (QObject child of MainWindow) has the scriptable slot
//    `QStringList sessionList()`, which walks container widgets 0..count-1 and
//    appends widget(i)->findChildren<TerminalDisplay*>() session ids.

#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include <dlfcn.h>
#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include <functional>
#include <string>

#include <QAction>
#include <QApplication>
#include <QColor>
#include <QColorDialog>
#include <QCoreApplication>
#include <QDBusConnection>
#include <QDBusConnectionInterface>
#include <QDBusReply>
#include <QDir>
#include <QEvent>
#include <QElapsedTimer>
#include <QFile>
#include <QFileSystemWatcher>
#include <QHash>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLinearGradient>
#include <QPaintEvent>
#include <QPainter>
#include <QPainterPath>
#include <QProxyStyle>
#include <QStyleFactory>
#include <QStyleOptionTab>
#include <QFileInfo>
#include <QInputDialog>
#include <QLineEdit>
#include <QMenu>
#include <QMetaObject>
#include <QPoint>
#include <QPointer>
#include <QProcess>
#include <QStandardPaths>
#include <QStringList>
#include <QTabBar>
#include <QTabWidget>
#include <QTimer>
#include <QVector>
#include <QWidget>

namespace {

constexpr const char kLibBasename[] = "libkonsole_aware_menu.so";
constexpr const char kInjectedProp[] = "konsole_aware_injected";

bool s_isKonsoleProcess = false;

// ---------------------------------------------------------------------------
// Process setup: keep LD_PRELOAD from leaking into every shell/tool spawned by
// Konsole, and only activate the hook inside Konsole itself.
// ---------------------------------------------------------------------------

void stripSelfFromLdPreload()
{
    const char *cur = getenv("LD_PRELOAD");
    if (!cur) {
        return;
    }
    const std::string in(cur);
    std::string out;
    bool removed = false;

    // ld.so accepts both ':' and whitespace as separators.
    size_t pos = 0;
    while (pos <= in.size()) {
        size_t end = in.find_first_of(": \t", pos);
        if (end == std::string::npos) {
            end = in.size();
        }
        const std::string tok = in.substr(pos, end - pos);
        if (!tok.empty()) {
            const size_t slash = tok.rfind('/');
            const std::string base = (slash == std::string::npos) ? tok : tok.substr(slash + 1);
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

__attribute__((constructor)) void konsoleAwareMenuInit()
{
    // Always strip ourselves (even in a non-Konsole process that somehow
    // inherited us) so that descendants do not load Qt6Widgets needlessly.
    stripSelfFromLdPreload();
    s_isKonsoleProcess = program_invocation_short_name && strcmp(program_invocation_short_name, "konsole") == 0;
}

bool isKonsoleProcess()
{
    if (s_isKonsoleProcess) {
        return true;
    }
    return QCoreApplication::instance() && QCoreApplication::applicationName() == QLatin1String("konsole");
}

// ---------------------------------------------------------------------------
// Tab titles: elide at the end, not the start
// ---------------------------------------------------------------------------
// Konsole's DetachableTabBar hardcodes Qt::ElideLeft, so a crowded tab bar cuts the
// *beginning* of every title, which is exactly where konsole-aware puts the agent
// identity (✳️ CLAUDE / 🪐 AGY / 👤 USER) and the project. Switch every Konsole tab bar
// to ElideRight as it is polished, so the identity always stays readable.
void installTabPatterns(QTabBar *bar);
void attachTerminalTint(QWidget *display);
void startPatternWatcher();
void startGrabWatcher();

class ElideRightFilter : public QObject
{
public:
    using QObject::QObject;

protected:
    bool eventFilter(QObject *obj, QEvent *ev) override
    {
        if (ev->type() == QEvent::Polish || ev->type() == QEvent::Show) {
            if (obj->inherits("Konsole::DetachableTabBar")) {
                auto *bar = static_cast<QTabBar *>(obj);
                if (bar->elideMode() != Qt::ElideRight && qEnvironmentVariableIsEmpty("KONSOLE_AWARE_KEEP_ELIDE")) {
                    bar->setElideMode(Qt::ElideRight);
                }
                installTabPatterns(bar);
            } else if (ev->type() == QEvent::Polish && obj->inherits("Konsole::TerminalDisplay")) {
                attachTerminalTint(static_cast<QWidget *>(obj));
            }
        }
        return false;
    }
};

void installElideFilter()
{
    if (!isKonsoleProcess() || !qApp) {
        return;
    }
    static ElideRightFilter *filter = nullptr;
    if (!filter) {
        filter = new ElideRightFilter(qApp);
        qApp->installEventFilter(filter);
        QTimer::singleShot(0, qApp, startPatternWatcher);
        QTimer::singleShot(0, qApp, startGrabWatcher);
    }
}
Q_COREAPP_STARTUP_FUNCTION(installElideFilter)

// ---------------------------------------------------------------------------
// Konsole private symbols
// ---------------------------------------------------------------------------

// Itanium C++ ABI: a non-virtual member function takes `this` as first arg.
using MoveTabToWindowFn = void (*)(QTabWidget *container, int tabIndex, QWidget *targetWindow);

MoveTabToWindowFn moveTabToWindowFn()
{
    static bool resolved = false;
    static MoveTabToWindowFn fn = nullptr;
    if (!resolved) {
        resolved = true;
        fn = reinterpret_cast<MoveTabToWindowFn>(dlsym(RTLD_DEFAULT, "_ZN7Konsole19TabbedViewContainer15moveTabToWindowEiP7QWidget"));
        if (!fn) {
            qWarning("konsole-aware: could not resolve TabbedViewContainer::moveTabToWindow");
        }
    }
    return fn;
}

// ---------------------------------------------------------------------------
// CLI helpers
// ---------------------------------------------------------------------------

QString resolveCli()
{
    const QString fromEnv = qEnvironmentVariable("KONSOLE_AWARE_BIN");
    if (!fromEnv.isEmpty()) {
        return fromEnv;
    }
    const QString local = QDir::homePath() + QStringLiteral("/.local/bin/konsole-aware");
    const QFileInfo fi(local);
    if (fi.isFile() && fi.isExecutable()) {
        return local;
    }
    const QString inPath = QStandardPaths::findExecutable(QStringLiteral("konsole-aware"));
    return inPath.isEmpty() ? QStringLiteral("konsole-aware") : inPath;
}

void runCli(const QStringList &args)
{
    const QString program = resolveCli();
    qint64 pid = 0;
    if (!QProcess::startDetached(program, args, QString(), &pid)) {
        qWarning("konsole-aware: failed to start '%s' %s", qPrintable(program), qPrintable(args.join(QLatin1Char(' '))));
    }
}

// D-Bus service name under which this Konsole process is reachable.
QString konsoleService()
{
    static QString cached;
    if (!cached.isEmpty()) {
        return cached;
    }
    const QString pidName = QStringLiteral("org.kde.konsole-%1").arg(QCoreApplication::applicationPid());

    QDBusConnection bus = QDBusConnection::sessionBus();
    if (!bus.isConnected()) {
        return pidName; // best guess, not cached
    }
    QDBusConnectionInterface *iface = bus.interface();
    const QString unique = bus.baseService();
    if (iface) {
        // KDBusService::Multiple registers org.kde.konsole-<pid>.
        const QDBusReply<bool> hasPidName = iface->isServiceRegistered(pidName);
        if (hasPidName.isValid() && hasPidName.value()) {
            return cached = pidName;
        }
        // KDBusService::Unique registers org.kde.konsole.
        const QDBusReply<QString> owner = iface->serviceOwner(QStringLiteral("org.kde.konsole"));
        if (owner.isValid() && !unique.isEmpty() && owner.value() == unique) {
            return cached = QStringLiteral("org.kde.konsole");
        }
    }
    if (!unique.isEmpty()) {
        return cached = unique;
    }
    return pidName;
}

void defer(std::function<void()> fn)
{
    // Run after the menu's event handling unwinds: some actions may delete the
    // tab bar (and therefore this menu) or open nested dialogs.
    QTimer::singleShot(0, qApp, std::move(fn));
}

// ---------------------------------------------------------------------------
// Tab -> session id resolution (public Qt APIs only)
// ---------------------------------------------------------------------------

int countTerminalDisplays(const QWidget *w)
{
    // Mirrors widget(i)->findChildren<TerminalDisplay*>() (recursive, children only).
    int n = 0;
    const auto children = w->findChildren<QWidget *>();
    for (const QWidget *c : children) {
        if (c->inherits("Konsole::TerminalDisplay")) {
            ++n;
        }
    }
    return n;
}

QStringList viewManagerSessionList(QObject *vm, bool *ok)
{
    QStringList ids;
    *ok = QMetaObject::invokeMethod(vm, "sessionList", Qt::DirectConnection, Q_RETURN_ARG(QStringList, ids));
    return ids;
}

// Returns the session id of the first terminal in tab `index` of `tw`, or -1.
int sessionIdForTab(QTabWidget *tw, int index)
{
    if (!tw || index < 0 || index >= tw->count()) {
        return -1;
    }

    int offset = 0;
    int total = 0;
    int inTab = 0;
    for (int i = 0; i < tw->count(); ++i) {
        QWidget *page = tw->widget(i);
        const int n = page ? countTerminalDisplays(page) : 0;
        if (i < index) {
            offset += n;
        } else if (i == index) {
            inTab = n;
        }
        total += n;
    }
    if (inTab == 0) {
        return -1;
    }

    QWidget *win = tw->window();
    if (!win) {
        return -1;
    }
    QList<QObject *> managers;
    const auto objs = win->findChildren<QObject *>();
    for (QObject *o : objs) {
        if (o->inherits("Konsole::ViewManager")) {
            managers.append(o);
        }
    }

    for (QObject *vm : std::as_const(managers)) {
        bool ok = false;
        const QStringList ids = viewManagerSessionList(vm, &ok);
        // The ViewManager driving this container lists exactly its terminals.
        if (!ok || ids.size() != total || offset >= ids.size()) {
            continue;
        }
        bool numOk = false;
        const int sid = ids.at(offset).toInt(&numOk);
        if (numOk) {
            return sid;
        }
    }
    qWarning("konsole-aware: could not map tab %d to a session id (%d view managers)", index, int(managers.size()));
    return -1;
}

// ---------------------------------------------------------------------------
// Context captured for the right-clicked tab
// ---------------------------------------------------------------------------

struct TabCtx {
    QPointer<QTabWidget> container; // Konsole::TabbedViewContainer
    QPointer<QWidget> page; // the tab's top-level ViewSplitter
    int sessionId = -1;
    QString service;

    bool hasSession() const
    {
        return sessionId >= 0 && !service.isEmpty();
    }

    // Current index of the tab (it may have moved since the menu opened).
    int currentIndex() const
    {
        if (!container || !page) {
            return -1;
        }
        return container->indexOf(page);
    }

    QWidget *dialogParent() const
    {
        return container ? container->window() : nullptr;
    }
};

// <head...> --tab N --service S [-- <positional...>]
void runTabCli(const TabCtx &ctx, const QStringList &head, const QStringList &positional = {})
{
    if (!ctx.hasSession()) {
        qWarning("konsole-aware: no session id for tab; not running '%s'", qPrintable(head.join(QLatin1Char(' '))));
        return;
    }
    QStringList args = head;
    args << QStringLiteral("--tab") << QString::number(ctx.sessionId) << QStringLiteral("--service") << ctx.service;
    if (!positional.isEmpty()) {
        args << QStringLiteral("--");
        args << positional;
    }
    runCli(args);
}

QAction *addTabCliAction(QMenu *menu, const QString &label, const TabCtx &ctx, const QStringList &head, const QStringList &positional = {})
{
    QAction *a = menu->addAction(label);
    a->setEnabled(ctx.hasSession());
    QObject::connect(a, &QAction::triggered, [ctx, head, positional]() {
        defer([ctx, head, positional]() {
            runTabCli(ctx, head, positional);
        });
    });
    return a;
}

void markInjected(QAction *a)
{
    a->setProperty(kInjectedProp, true);
}

void removeInjected(QMenu *menu, bool deferred)
{
    const QList<QAction *> actions = menu->actions();
    for (QAction *act : actions) {
        if (!act || !act->property(kInjectedProp).toBool()) {
            continue;
        }
        menu->removeAction(act);
        if (QMenu *sub = QMenu::menuInAction(act)) {
            // The submenu owns its menuAction; deleting it releases both.
            if (deferred) {
                sub->deleteLater();
            } else {
                delete sub;
            }
        } else if (deferred) {
            act->deleteLater();
        } else {
            delete act;
        }
    }
}

bool looksLikeKonsoleTabMenu(QMenu *menu)
{
    const auto actions = menu->actions();
    for (QAction *act : actions) {
        if (!act) {
            continue;
        }
        const QString on = act->objectName();
        if (on == QLatin1String("tab-detach") || on == QLatin1String("edit-rename") || on == QLatin1String("tab-close")) {
            return true;
        }
    }
    // Fallback for builds without objectNames (English UI only).
    for (QAction *act : actions) {
        if (!act) {
            continue;
        }
        const QString text = act->text().remove(QLatin1Char('&'));
        if (text.contains(QLatin1String("Detach Tab"), Qt::CaseInsensitive)
            || text.contains(QLatin1String("Configure or Rename Tab"), Qt::CaseInsensitive)) {
            return true;
        }
    }
    return false;
}

QWidget *findViewManagerOwner(QWidget *window)
{
    // moveTabToWindow() does window->findChild<ViewManager*>() and dereferences it.
    const auto objs = window->findChildren<QObject *>();
    for (QObject *o : objs) {
        if (o->inherits("Konsole::ViewManager")) {
            return window;
        }
    }
    return nullptr;
}

// Session id of the first pane of every tab, computed with one ViewManager lookup.
QVector<int> allTabSessionIds(QTabWidget *tw)
{
    QVector<int> out;
    if (!tw || tw->tabBar()->count() != tw->count()) {
        return out; // mid-move: Konsole's own lists disagree, don't ask it now
    }
    for (int i = 0; i < tw->count(); ++i) {
        if (!tw->widget(i)) {
            return out;
        }
    }
    QVector<int> offsets;
    int total = 0;
    for (int i = 0; i < tw->count(); ++i) {
        offsets.append(total);
        QWidget *page = tw->widget(i);
        total += page ? countTerminalDisplays(page) : 0;
    }
    QWidget *win = tw->window();
    QStringList ids;
    if (win) {
        const auto objs = win->findChildren<QObject *>();
        for (QObject *o : objs) {
            if (!o->inherits("Konsole::ViewManager")) {
                continue;
            }
            bool ok = false;
            const QStringList cand = viewManagerSessionList(o, &ok);
            if (ok && cand.size() == total) {
                ids = cand;
                break;
            }
        }
    }
    for (int i = 0; i < tw->count(); ++i) {
        bool ok = false;
        const int sid = (offsets[i] < ids.size()) ? ids.at(offsets[i]).toInt(&ok) : -1;
        out.append(ok ? sid : -1);
    }
    return out;
}


// ---------------------------------------------------------------------------
// Patterned tab backgrounds (solid, two-tone, gradient, rainbow, stripes)
// ---------------------------------------------------------------------------
// Konsole only draws a tab's color as a 4 px line under the title. konsole-aware
// publishes a background spec per session in $XDG_RUNTIME_DIR/konsole-aware/styles/tab-styles.json;
// a QProxyStyle on each tab bar fills the tab with it and draws the title in black or
// white, whichever reads better on that pattern. The selected tab is drawn at full
// strength with an accent bar, and the terminal under each tab is tinted to match.

bool patternDebug()
{
    static const bool on = !qEnvironmentVariableIsEmpty("KONSOLE_AWARE_DEBUG");
    return on;
}

struct TabPattern {
    QString pattern;
    QVector<QColor> colors;
    double opacity = 0.6;
    double tint = 0.0; // terminal background tint strength, 0 = off
    bool band = true;  // strip of the tab's colors along the top of its terminal
};

QHash<QString, TabPattern> s_patterns; // "org.kde.konsole-<pid>:/Sessions/<n>" -> pattern
bool s_patternsLoaded = false;
bool s_followPosition = false; // color tabs by position instead of by session
QVector<QColor> s_positionPalette;

QString stylesFilePath()
{
    QString dir = qEnvironmentVariable("KONSOLE_AWARE_STATE_DIR");
    if (dir.isEmpty()) {
        const QString runtime = qEnvironmentVariable("XDG_RUNTIME_DIR");
        dir = runtime.isEmpty() ? QStringLiteral("/tmp/konsole-aware-%1").arg(getuid()) : runtime + QStringLiteral("/konsole-aware");
    }
    return dir + QStringLiteral("/styles/tab-styles.json");
}

QVector<QColor> parseColors(const QJsonArray &arr)
{
    QVector<QColor> out;
    for (const auto &c : arr) {
        const QColor col(c.toString());
        if (col.isValid()) {
            out.append(col);
        }
    }
    return out;
}

void loadPatterns()
{
    s_patterns.clear();
    s_patternsLoaded = true;
    s_followPosition = false;
    s_positionPalette.clear();
    QFile f(stylesFilePath());
    if (!f.open(QIODevice::ReadOnly)) {
        return;
    }
    const QJsonObject root = QJsonDocument::fromJson(f.readAll()).object();
    if (patternDebug()) {
        qWarning("konsole-aware: loaded %d tab patterns from %s", int(root.size()), qPrintable(stylesFilePath()));
    }
    for (auto it = root.begin(); it != root.end(); ++it) {
        const QJsonObject o = it.value().toObject();
        if (it.key() == QLatin1String("_settings")) {
            s_followPosition = o.value(QStringLiteral("follow_position")).toBool(false);
            s_positionPalette = parseColors(o.value(QStringLiteral("palette")).toArray());
            continue;
        }
        TabPattern p;
        p.pattern = o.value(QStringLiteral("pattern")).toString();
        p.opacity = qBound(0.15, o.value(QStringLiteral("opacity")).toDouble(0.6), 1.0);
        p.tint = qBound(0.0, o.value(QStringLiteral("tint")).toDouble(0.0), 0.5);
        p.band = o.value(QStringLiteral("band")).toBool(true);
        p.colors = parseColors(o.value(QStringLiteral("colors")).toArray());
        if (!p.colors.isEmpty() && p.pattern != QLatin1String("none")) {
            s_patterns.insert(it.key(), p);
        }
    }
}

void refreshTerminals(bool repaint = true);

void repaintTabBars()
{
    const auto tops = QApplication::topLevelWidgets();
    for (QWidget *w : tops) {
        const auto bars = w->findChildren<QTabBar *>();
        for (QTabBar *bar : bars) {
            if (bar->inherits("Konsole::DetachableTabBar")) {
                bar->update();
            }
        }
    }
    refreshTerminals();
}

void startPatternWatcher()
{
    static QFileSystemWatcher *watcher = nullptr;
    if (watcher || !isKonsoleProcess()) {
        return;
    }
    const QString file = stylesFilePath();
    QDir().mkpath(QFileInfo(file).absolutePath());
    watcher = new QFileSystemWatcher(qApp);
    watcher->addPath(QFileInfo(file).absolutePath()); // the file is replaced atomically
    if (QFile::exists(file)) {
        watcher->addPath(file);
    }
    // Coalesce bursts of changes into one reload and one repaint.
    static QTimer *debounce = nullptr;
    debounce = new QTimer(qApp);
    debounce->setSingleShot(true);
    debounce->setInterval(150);
    QObject::connect(debounce, &QTimer::timeout, qApp, [file]() {
        loadPatterns();
        if (QFile::exists(file) && !watcher->files().contains(file)) {
            watcher->addPath(file);
        }
        repaintTabBars();
    });
    auto reload = []() { debounce->start(); };
    QObject::connect(watcher, &QFileSystemWatcher::directoryChanged, qApp, reload);
    QObject::connect(watcher, &QFileSystemWatcher::fileChanged, qApp, reload);
    loadPatterns();
}

// README screenshots: with KONSOLE_AWARE_GRAB=/path/shot.png set, creating
// /path/shot.png.request saves each Konsole window to shot.png, shot-2.png, ... It
// works under QT_QPA_PLATFORM=offscreen, so nothing shows up on the desktop.
void startGrabWatcher()
{
    const QString target = qEnvironmentVariable("KONSOLE_AWARE_GRAB");
    if (target.isEmpty() || !isKonsoleProcess()) {
        return;
    }
    auto *timer = new QTimer(qApp);
    timer->setInterval(300);
    QObject::connect(timer, &QTimer::timeout, qApp, [target]() {
        const QString request = target + QStringLiteral(".request");
        if (!QFile::exists(request)) {
            return;
        }
        QFile::remove(request);
        int n = 0;
        const auto tops = QApplication::topLevelWidgets();
        for (QWidget *w : tops) {
            if (!w->isVisible() || !w->inherits("Konsole::MainWindow")) {
                continue;
            }
            QString path = target;
            if (n > 0) {
                path.replace(QStringLiteral(".png"), QStringLiteral("-%1.png").arg(n + 1));
            }
            w->grab().save(path);
            ++n;
        }
    });
    timer->start();
}

// Session id of each tab, cached per tab bar for a second (paint runs often). Moving a
// tab drops the cache, so a dragged tab keeps its own colors.
struct TabIdCache {
    QElapsedTimer age;
    QVector<const QWidget *> pages; // tab pages when cached: any move or swap invalidates
    QVector<int> ids;
};

bool samePages(const QTabWidget *tw, const QVector<const QWidget *> &pages)
{
    if (pages.size() != tw->count()) {
        return false;
    }
    for (int i = 0; i < pages.size(); ++i) {
        if (tw->widget(i) != pages.at(i)) {
            return false;
        }
    }
    return true;
}
QHash<const QTabBar *, TabIdCache> s_tabIds;

// The pattern for tab `index` of `bar`. Patterns belong to the tab's first session, so
// every pane of a split tab (tabs joined with attach or pair) shares the tab's colors.
bool patternForTab(const QTabBar *bar, int index, TabPattern *out)
{
    if (index < 0) {
        return false;
    }
    if (!s_patternsLoaded) {
        loadPatterns();
    }
    if (s_patterns.isEmpty()) {
        return false;
    }
    auto *tw = qobject_cast<QTabWidget *>(bar->parentWidget());
    if (!tw) {
        return false;
    }
    TabIdCache &cache = s_tabIds[bar];
    if (!samePages(tw, cache.pages) || !cache.age.isValid() || cache.age.elapsed() > 1000) {
        // Konsole moves a tab by removing and re-inserting its neighbor, so the count alone
        // does not show that tabs changed places; compare the pages themselves.
        cache.ids = allTabSessionIds(tw);
        if (cache.ids.isEmpty()) {
            return false; // asked mid-move; look again on the next paint
        }
        cache.pages.clear();
        for (int i = 0; i < tw->count(); ++i) {
            cache.pages.append(tw->widget(i));
        }
        cache.age.restart();
    }
    if (index >= cache.ids.size() || cache.ids.at(index) < 0) {
        return false;
    }
    // konsole-aware keys tabs by the well-known name org.kde.konsole-<pid>. Don't use the
    // cached konsoleService() here: the first paint happens before Konsole registers that
    // name on D-Bus, so the cache would hold the temporary unique name forever.
    const QString suffix = QStringLiteral(":/Sessions/") + QString::number(cache.ids.at(index));
    auto it = s_patterns.constFind(QStringLiteral("org.kde.konsole-%1").arg(QCoreApplication::applicationPid()) + suffix);
    if (it == s_patterns.constEnd()) {
        it = s_patterns.constFind(QStringLiteral("org.kde.konsole") + suffix);
    }
    if (patternDebug()) {
        qWarning("konsole-aware: tab %d -> session %d: %s", index, cache.ids.at(index),
                 it == s_patterns.constEnd() ? "no pattern" : qPrintable(it.value().pattern));
    }
    if (it == s_patterns.constEnd()) {
        return false;
    }
    *out = it.value();
    if (s_followPosition && !s_positionPalette.isEmpty() && out->pattern != QLatin1String("rainbow")) {
        // Opt-in: the first color comes from the tab's position, so dragging recolors it.
        out->colors[0] = s_positionPalette.at(index % s_positionPalette.size());
    }
    return true;
}

// Tab index and container of the tab that holds `w` (a terminal), or -1.
int tabIndexOf(QWidget *w, QTabWidget **container)
{
    for (QWidget *page = w; page; page = page->parentWidget()) {
        QWidget *stack = page->parentWidget();
        QWidget *parent = stack ? stack->parentWidget() : nullptr;
        if (parent && parent->inherits("Konsole::TabbedViewContainer")) {
            auto *tw = static_cast<QTabWidget *>(parent);
            const int index = tw->indexOf(page);
            if (index >= 0) {
                *container = tw;
                return index;
            }
        }
    }
    return -1;
}

bool patternForTerminal(QWidget *display, TabPattern *out, QTabWidget **container = nullptr, int *index = nullptr)
{
    QTabWidget *tw = nullptr;
    const int i = tabIndexOf(display, &tw);
    if (container) {
        *container = tw;
    }
    if (index) {
        *index = i;
    }
    return i >= 0 && patternForTab(tw->tabBar(), i, out);
}

QColor blend(const QColor &a, const QColor &b, double t)
{
    return QColor::fromRgbF(a.redF() * (1 - t) + b.redF() * t, a.greenF() * (1 - t) + b.greenF() * t,
                            a.blueF() * (1 - t) + b.blueF() * t);
}

double luminance(const QColor &c)
{
    return 0.2126 * c.redF() + 0.7152 * c.greenF() + 0.0722 * c.blueF();
}

// The selected tab is drawn at full strength; the others at the configured strength,
// softened, so the selected one stands out.
double tabOpacity(const TabPattern &p, bool selected)
{
    return selected ? 1.0 : p.opacity * 0.75;
}

QColor textColorFor(const TabPattern &p, const QColor &background, double opacity)
{
    double r = 0, g = 0, b = 0;
    for (const QColor &c : p.colors) {
        r += c.redF();
        g += c.greenF();
        b += c.blueF();
    }
    const int n = p.colors.size();
    const QColor avg = blend(background, QColor::fromRgbF(r / n, g / n, b / n), opacity);
    return luminance(avg) > 0.55 ? QColor(20, 20, 20) : QColor(250, 250, 250);
}

// Fill `r` with the pattern (no clipping, opacity, or shape: callers set those).
void fillPattern(QPainter *p, const QRectF &r, const TabPattern &pat)
{
    const auto &c = pat.colors;
    if (pat.pattern == QLatin1String("two-tone") && c.size() >= 2) {
        const qreal mid = r.left() + r.width() / 2;
        p->fillRect(QRectF(r.left(), r.top(), mid - r.left(), r.height()), c[0]);
        p->fillRect(QRectF(mid, r.top(), r.right() - mid, r.height()), c[1]);
    } else if (pat.pattern == QLatin1String("stripes") && c.size() >= 2) {
        p->fillRect(r, c[0]);
        const qreal w = 10;
        int i = 0;
        p->setPen(Qt::NoPen);
        for (qreal x = r.left() - r.height(); x < r.right(); x += w, ++i) {
            QPolygonF band;
            band << QPointF(x, r.bottom()) << QPointF(x + w, r.bottom()) << QPointF(x + w + r.height(), r.top())
                 << QPointF(x + r.height(), r.top());
            p->setBrush(c[i % c.size()]);
            p->drawPolygon(band);
        }
    } else if ((pat.pattern == QLatin1String("gradient") || pat.pattern == QLatin1String("rainbow")) && c.size() >= 2) {
        QLinearGradient grad(r.topLeft(), r.topRight());
        for (int i = 0; i < c.size(); ++i) {
            grad.setColorAt(double(i) / (c.size() - 1), c[i]);
        }
        p->fillRect(r, grad);
    } else {
        p->fillRect(r, c[0]);
    }
}

void paintPattern(QPainter *p, const QRect &tabRect, const TabPattern &pat, bool selected, QTabBar::Shape shape,
                  const QColor &accent)
{
    const bool north = shape == QTabBar::RoundedNorth || shape == QTabBar::TriangularNorth;
    const bool south = shape == QTabBar::RoundedSouth || shape == QTabBar::TriangularSouth;
    // The selected tab fills its whole cell and touches the terminal below it (above it for
    // a bottom tab bar), so it reads as joined to its terminal. Unselected tabs sit inset.
    QRectF r = selected ? QRectF(tabRect).adjusted(1, north ? 1 : 0, -1, south ? -1 : 0)
                        : QRectF(tabRect).adjusted(3, 5, -3, -3);
    QPainterPath path;
    path.addRoundedRect(r, 5, 5);
    if (selected && (north || south)) {
        // Square the corners that meet the terminal.
        path.addRect(north ? r.adjusted(0, r.height() / 2, 0, 0) : r.adjusted(0, 0, 0, -r.height() / 2));
        path.setFillRule(Qt::WindingFill);
        path = path.simplified();
    }
    p->save();
    p->setRenderHint(QPainter::Antialiasing, true);
    p->setClipPath(path);
    p->setOpacity(tabOpacity(pat, selected));
    p->setPen(Qt::NoPen);
    fillPattern(p, r, pat);
    if (selected) {
        // Accent bar on the outer edge, in the title color so it shows on any pattern.
        p->setOpacity(0.9);
        const qreal h = 3;
        const QRectF bar = south ? QRectF(r.left(), r.bottom() - h, r.width(), h) : QRectF(r.left(), r.top(), r.width(), h);
        p->fillRect(bar, accent);
    }
    p->restore();
}

class TabPatternStyle : public QProxyStyle
{
public:
    using QProxyStyle::QProxyStyle;

    void drawControl(ControlElement element, const QStyleOption *option, QPainter *painter, const QWidget *widget) const override
    {
        const auto *tab = qstyleoption_cast<const QStyleOptionTab *>(option);
        const auto *bar = qobject_cast<const QTabBar *>(widget);
        if (tab && bar && (element == CE_TabBarTabShape || element == CE_TabBarTabLabel)) {
            // tabIndex is the tab being drawn. tabAt(rect.center()) is wrong while a tab is
            // dragged or sliding into place: it finds whichever tab is under that point.
            const int index = tab->tabIndex >= 0 ? tab->tabIndex : bar->tabAt(tab->rect.center());
            TabPattern pat;
            if (patternForTab(bar, index, &pat)) {
                const bool selected = tab->state & State_Selected;
                const QColor fg = textColorFor(pat, bar->palette().color(QPalette::Window), tabOpacity(pat, selected));
                if (element == CE_TabBarTabShape) {
                    QProxyStyle::drawControl(element, option, painter, widget);
                    paintPattern(painter, tab->rect, pat, selected, tab->shape, fg);
                    return;
                }
                QStyleOptionTab label(*tab);
                for (auto role : {QPalette::WindowText, QPalette::ButtonText, QPalette::Text, QPalette::BrightText}) {
                    label.palette.setColor(role, fg);
                }
                QProxyStyle::drawControl(element, &label, painter, widget);
                return;
            }
        }
        QProxyStyle::drawControl(element, option, painter, widget);
    }
};

// ---------------------------------------------------------------------------
// Terminal tint: carry the tab's colors into the terminal under it
// ---------------------------------------------------------------------------
// After Konsole paints a terminal, its background is tinted with the tab's colors:
// Screen blending on a dark color scheme, Multiply on a light one, so the background
// takes the color while text keeps its contrast. The tint only varies left to right,
// which keeps it correct when Konsole scrolls by copying pixels up or down. A thin strip
// of the tab's pattern runs along the top of the terminal; in a split tab the focused
// pane's strip is drawn at full strength and the others faintly.

constexpr int kBandHeight = 3;
constexpr const char kBandName[] = "konsole_aware_band";

class TerminalBand : public QWidget
{
public:
    explicit TerminalBand(QWidget *parent)
        : QWidget(parent)
    {
        setObjectName(QLatin1String(kBandName));
        setAttribute(Qt::WA_TransparentForMouseEvents);
        setAttribute(Qt::WA_NoSystemBackground);
        setFocusPolicy(Qt::NoFocus);
    }

    TabPattern pattern;
    bool strong = true;

protected:
    void paintEvent(QPaintEvent *) override
    {
        QPainter p(this);
        p.setOpacity(strong ? 1.0 : 0.35);
        fillPattern(&p, QRectF(rect()), pattern);
    }
};

int terminalsIn(QWidget *page)
{
    return page ? countTerminalDisplays(page) : 0;
}

void syncBand(QWidget *display)
{
    // TerminalBand has no Q_OBJECT, so find it by object name.
    auto *band = static_cast<TerminalBand *>(display->findChild<QWidget *>(QLatin1String(kBandName), Qt::FindDirectChildrenOnly));
    TabPattern pat;
    QTabWidget *tw = nullptr;
    int index = -1;
    if (!patternForTerminal(display, &pat, &tw, &index) || !pat.band) {
        if (band) {
            band->hide();
        }
        return;
    }
    if (!band) {
        band = new TerminalBand(display);
    }
    band->pattern = pat;
    QWidget *focus = display->window() ? display->window()->focusWidget() : nullptr;
    band->strong = focus == display || terminalsIn(tw->widget(index)) <= 1;
    band->setGeometry(0, 0, display->width(), kBandHeight);
    band->raise();
    band->show();
    band->update();
}

void paintTint(QWidget *display, const QRegion &region, const TabPattern &pat)
{
    QPainter p(display);
    p.setClipRegion(region);
    const bool dark = luminance(display->palette().color(display->backgroundRole())) < 0.5;
    p.setCompositionMode(dark ? QPainter::CompositionMode_Screen : QPainter::CompositionMode_Multiply);
    p.setOpacity(pat.tint);
    const QRectF r(display->rect());
    if (pat.colors.size() >= 2) {
        // Patterns become a smooth left-to-right blend: hard edges would split the text area.
        QLinearGradient grad(r.topLeft(), r.topRight());
        for (int i = 0; i < pat.colors.size(); ++i) {
            grad.setColorAt(double(i) / (pat.colors.size() - 1), pat.colors[i]);
        }
        p.fillRect(r, grad);
    } else {
        p.fillRect(r, pat.colors[0]);
    }
}

// One refresh after the event loop settles, however many events asked for it.
void scheduleTerminalRefresh(bool repaint)
{
    static QTimer *timer = nullptr;
    static bool pendingRepaint = false;
    pendingRepaint = pendingRepaint || repaint;
    if (!timer) {
        timer = new QTimer(qApp);
        timer->setSingleShot(true);
        timer->setInterval(30);
        QObject::connect(timer, &QTimer::timeout, qApp, []() {
            const bool r = pendingRepaint;
            pendingRepaint = false;
            refreshTerminals(r);
        });
    }
    timer->start();
}

class TerminalTintFilter : public QObject
{
public:
    using QObject::QObject;

protected:
    bool eventFilter(QObject *obj, QEvent *ev) override
    {
        switch (ev->type()) {
        case QEvent::Paint: {
            auto *display = static_cast<QWidget *>(obj);
            TabPattern pat;
            if (!patternForTerminal(display, &pat) || pat.tint <= 0) {
                return false;
            }
            obj->event(ev); // Konsole paints the terminal, then the tint goes on top
            paintTint(display, static_cast<QPaintEvent *>(ev)->region(), pat);
            return true;
        }
        case QEvent::Resize: {
            // Only resize here. These events also arrive while Konsole is in the middle of
            // moving or closing a tab, when asking it for its sessions crashes it, so the
            // pattern lookup waits for the queued refresh.
            auto *display = static_cast<QWidget *>(obj);
            if (QWidget *band = display->findChild<QWidget *>(QLatin1String(kBandName), Qt::FindDirectChildrenOnly)) {
                band->setGeometry(0, 0, display->width(), kBandHeight);
            }
            scheduleTerminalRefresh(false);
            break;
        }
        case QEvent::Show:
        case QEvent::ParentChange:
        case QEvent::FocusIn:
        case QEvent::FocusOut: // brightens the focused pane's strip in a split tab
            scheduleTerminalRefresh(false);
            break;
        default:
            break;
        }
        return false;
    }
};

void attachTerminalTint(QWidget *display)
{
    if (display->property("konsole_aware_tint").toBool() || !qEnvironmentVariableIsEmpty("KONSOLE_AWARE_NO_PATTERNS")) {
        return;
    }
    static TerminalTintFilter *filter = new TerminalTintFilter(qApp);
    display->setProperty("konsole_aware_tint", true);
    display->installEventFilter(filter);
}

// Repaints terminals too when `repaint` (colors changed); otherwise only the strips.
void refreshTerminals(bool repaint)
{
    const auto tops = QApplication::topLevelWidgets();
    for (QWidget *top : tops) {
        if (!top->inherits("Konsole::MainWindow")) {
            continue;
        }
        const auto children = top->findChildren<QWidget *>();
        for (QWidget *w : children) {
            if (w->inherits("Konsole::TerminalDisplay")) {
                attachTerminalTint(w);
                syncBand(w);
                if (repaint) {
                    w->update();
                }
            }
        }
    }
}

void installTabPatterns(QTabBar *bar)
{
    if (!bar || bar->property("konsole_aware_patterns").toBool() || !qEnvironmentVariableIsEmpty("KONSOLE_AWARE_NO_PATTERNS")) {
        return;
    }
    bar->setProperty("konsole_aware_patterns", true);
    QStyle *base = QStyleFactory::create(qApp->style()->name());
    if (!base) {
        base = QStyleFactory::create(QStringLiteral("Fusion"));
    }
    if (patternDebug()) {
        qWarning("konsole-aware: tab bar style %s -> %s", qPrintable(qApp->style()->name()), base ? "pattern style installed" : "no base style");
    }
    if (!base) {
        return;
    }
    auto *style = new TabPatternStyle(base);
    style->setParent(bar);
    bar->setStyle(style);
    QObject::connect(bar, &QObject::destroyed, qApp, [bar]() { s_tabIds.remove(bar); });
    // A moved tab keeps its colors: forget the cached tab -> session map at once.
    QObject::connect(bar, &QTabBar::tabMoved, bar, [bar](int, int) {
        s_tabIds.remove(bar);
        bar->update();
        scheduleTerminalRefresh(true);
    });
    QObject::connect(bar, &QTabBar::currentChanged, bar, [](int) { scheduleTerminalRefresh(false); });
}

// "Attach Tab To…", placed right after Konsole's own "Detach Tab".
void injectAttachMenu(QMenu *self, const TabCtx &ctx)
{
    QTabWidget *tw = ctx.container;
    if (!tw) {
        return;
    }
    QAction *detach = nullptr;
    const auto actions = self->actions();
    for (int i = 0; i < actions.size(); ++i) {
        if (actions[i] && actions[i]->objectName() == QLatin1String("tab-detach")) {
            detach = (i + 1 < actions.size()) ? actions[i + 1] : nullptr;
            break;
        }
    }

    QMenu *menu = new QMenu(QStringLiteral("Attach Tab To…"), self);
    const QVector<int> sids = allTabSessionIds(tw);
    const int me = ctx.currentIndex();
    QTabBar *bar = tw->tabBar();
    int added = 0;
    for (int i = 0; i < tw->count(); ++i) {
        if (i == me || i >= sids.size() || sids[i] < 0) {
            continue;
        }
        QString text = bar ? bar->tabText(i) : QString();
        text.replace(QLatin1Char('&'), QStringLiteral("&&"));
        if (text.size() > 60) {
            text = text.left(59) + QChar(0x2026);
        }
        addTabCliAction(menu, QStringLiteral("%1. %2").arg(i + 1).arg(text), ctx,
                        {QStringLiteral("attach"), QStringLiteral("--to"), QString::number(sids[i])});
        ++added;
    }
    if (added == 0) {
        menu->addAction(QStringLiteral("(No other tabs in this window)"))->setEnabled(false);
    }
    menu->addSeparator();
    addTabCliAction(menu, QStringLiteral("Pair This Project's Agents Side by Side"), ctx, {QStringLiteral("pair")});
    QAction *paneOut = addTabCliAction(menu, QStringLiteral("Split This Pane Out to a New Window"), ctx,
                                       {QStringLiteral("detach-pane")});
    paneOut->setEnabled(ctx.hasSession() && ctx.page && countTerminalDisplays(ctx.page) > 1);

    QAction *menuAction = menu->menuAction();
    markInjected(menuAction);
    if (detach) {
        self->insertAction(detach, menuAction);
    } else {
        self->addAction(menuAction);
    }
}

void injectActions(QMenu *self, const TabCtx &ctx)
{
    injectAttachMenu(self, ctx);

    QAction *sep = self->addSeparator();
    markInjected(sep);

    // 1. Anchor / pin
    markInjected(addTabCliAction(self, QStringLiteral("Anchor / Pin Tab"), ctx, {QStringLiteral("anchor")}));
    markInjected(addTabCliAction(self, QStringLiteral("Unpin Tab"), ctx, {QStringLiteral("unanchor")}));

    // 2. Snap project tabs together
    markInjected(addTabCliAction(self, QStringLiteral("Snap Project Tabs Together"), ctx, {QStringLiteral("snap")}));
    markInjected(addTabCliAction(self, QStringLiteral("Refresh All Tabs"), ctx, {QStringLiteral("refresh")}));

    // 3. Colour
    QMenu *menuColor = self->addMenu(QStringLiteral("Tab Color"));
    markInjected(menuColor->menuAction());
    struct ColorChoice {
        const char *label;
        const char *value;
    };
    static const ColorChoice s_colors[] = {
        {"🟢 Emerald (Done / Success)", "#10b981"},
        {"🟡 Amber (Busy / Working)", "#f59e0b"},
        {"🔴 Red (Error / Alert)", "#ef4444"},
        {"🟣 Purple (Waiting / Attention)", "#8b5cf6"},
        {"🔵 Blue (Idle / Shell)", "#3b82f6"},
        {"⚪ Default / Reset Color", "reset"},
    };
    for (const auto &c : s_colors) {
        addTabCliAction(menuColor, QString::fromUtf8(c.label), ctx, {QStringLiteral("set-color"), QString::fromUtf8(c.value)});
    }

    menuColor->addSeparator();
    QMenu *menuAgentColors = menuColor->addMenu(QStringLiteral("Agent Brand Colors"));
    markInjected(menuAgentColors->menuAction());
    static const ColorChoice s_agentColors[] = {
        {"✳️ Claude Terracotta", "#d97757"},
        {"🪐 AGY Electric Blue", "#4285f4"},
        {"♊ Gemini Purple", "#8e75ff"},
        {"🧩 Codex Green", "#10a37f"},
        {"🤝 Aider Teal", "#14b8a6"},
        {"🛩️ Copilot Purple", "#6e40c9"},
        {"🔓 OpenCode Orange", "#f97316"},
        {"🙌 OpenHands Cyan", "#06b6d4"},
    };
    for (const auto &ac : s_agentColors) {
        addTabCliAction(menuAgentColors, QString::fromUtf8(ac.label), ctx, {QStringLiteral("set-color"), QString::fromUtf8(ac.value)});
    }

    menuColor->addSeparator();
    QAction *actCustomColor = menuColor->addAction(QStringLiteral("Custom Color…"));
    actCustomColor->setEnabled(ctx.hasSession());
    QObject::connect(actCustomColor, &QAction::triggered, [ctx]() {
        defer([ctx]() {
            const QColor chosen = QColorDialog::getColor(Qt::white, ctx.dialogParent(), QStringLiteral("Select Tab Color"));
            if (chosen.isValid()) {
                runTabCli(ctx, {QStringLiteral("set-color"), chosen.name()});
            }
        });
    });

    // 3b. Tab pattern (painted by this library; see tab-styles.json)
    QMenu *menuPattern = self->addMenu(QStringLiteral("Tab Pattern"));
    markInjected(menuPattern->menuAction());
    struct PatternChoice {
        const char *label;
        const char *pattern;
    };
    static const PatternChoice s_patternChoices[] = {
        {"Two-Tone (project | status)", "two-tone"},
        {"Gradient", "gradient"},
        {"Rainbow", "rainbow"},
        {"Stripes", "stripes"},
        {"Solid", "solid"},
    };
    for (const auto &pc : s_patternChoices) {
        addTabCliAction(menuPattern, QString::fromUtf8(pc.label), ctx,
                        {QStringLiteral("tab-style"), QString::fromUtf8(pc.pattern)});
    }
    QAction *actPatternColors = menuPattern->addAction(QStringLiteral("Custom Gradient…"));
    actPatternColors->setEnabled(ctx.hasSession());
    QObject::connect(actPatternColors, &QAction::triggered, [ctx]() {
        defer([ctx]() {
            QWidget *parent = ctx.dialogParent();
            const QColor a = QColorDialog::getColor(QColor(QStringLiteral("#2563eb")), parent, QStringLiteral("Gradient start"));
            if (!a.isValid()) {
                return;
            }
            const QColor b = QColorDialog::getColor(QColor(QStringLiteral("#db2777")), parent, QStringLiteral("Gradient end"));
            if (!b.isValid()) {
                return;
            }
            runTabCli(ctx, {QStringLiteral("tab-style"), QStringLiteral("gradient"), a.name(), b.name()});
        });
    });
    menuPattern->addSeparator();
    addTabCliAction(menuPattern, QStringLiteral("Back to Project Colors"), ctx, {QStringLiteral("tab-style"), QStringLiteral("--clear")});
    addTabCliAction(menuPattern, QStringLiteral("Color and Group All Projects"), ctx, {QStringLiteral("colors"), QStringLiteral("auto")});
    QAction *actFollow = addTabCliAction(menuPattern, QStringLiteral("Colors Follow Tab Position"), ctx,
                                         {QStringLiteral("colors"), QStringLiteral("position"),
                                          s_followPosition ? QStringLiteral("off") : QStringLiteral("on")});
    actFollow->setCheckable(true);
    actFollow->setChecked(s_followPosition);

    // 4. Group
    QMenu *menuGroup = self->addMenu(QStringLiteral("Assign Group"));
    markInjected(menuGroup->menuAction());
    struct GroupChoice {
        const char *label;
        const char *name;
        const char *color;
        const char *icon;
    };
    static const GroupChoice s_groups[] = {
        {"🤖 Agents", "agents", "#8b5cf6", "🤖"},
        {"🎨 Frontend", "frontend", "#3b82f6", "🎨"},
        {"⚙️ Backend", "backend", "#10b981", "⚙️"},
        {"📊 Data", "data", "#eab308", "📊"},
        {"☁️ Infrastructure", "infra", "#06b6d4", "☁️"},
    };
    for (const auto &g : s_groups) {
        addTabCliAction(menuGroup,
                        QString::fromUtf8(g.label),
                        ctx,
                        {QStringLiteral("group"),
                         QStringLiteral("set"),
                         QString::fromUtf8(g.name),
                         QStringLiteral("--color"),
                         QString::fromUtf8(g.color),
                         QStringLiteral("--icon"),
                         QString::fromUtf8(g.icon)});
    }
    QAction *actCustomGroup = menuGroup->addAction(QStringLiteral("Custom Group…"));
    actCustomGroup->setEnabled(ctx.hasSession());
    QObject::connect(actCustomGroup, &QAction::triggered, [ctx]() {
        defer([ctx]() {
            bool ok = false;
            const QString name =
                QInputDialog::getText(ctx.dialogParent(), QStringLiteral("Custom Tab Group"), QStringLiteral("Enter group name:"), QLineEdit::Normal, QString(), &ok)
                    .trimmed();
            if (ok && !name.isEmpty()) {
                runTabCli(ctx, {QStringLiteral("group"), QStringLiteral("set")}, {name});
            }
        });
    });
    addTabCliAction(menuGroup, QStringLiteral("Remove from Group"), ctx, {QStringLiteral("group"), QStringLiteral("remove")});

    // 5. Project & job context
    QMenu *menuJob = self->addMenu(QStringLiteral("Project Name"));
    markInjected(menuJob->menuAction());
    QAction *actSetJob = menuJob->addAction(QStringLiteral("Set Project Name…"));
    actSetJob->setEnabled(ctx.hasSession());
    QObject::connect(actSetJob, &QAction::triggered, [ctx]() {
        defer([ctx]() {
            bool ok = false;
            const QString text = QInputDialog::getText(ctx.dialogParent(),
                                                       QStringLiteral("Set Job Name"),
                                                       QStringLiteral("Enter permanent project or job name:"),
                                                       QLineEdit::Normal,
                                                       QString(),
                                                       &ok)
                                     .trimmed();
            if (ok && !text.isEmpty()) {
                runTabCli(ctx, {QStringLiteral("set-job")}, {text});
            }
        });
    });
    addTabCliAction(menuJob, QStringLiteral("Back to the Automatic Name"), ctx, {QStringLiteral("set-job"), QStringLiteral("--clear")});

    // 6. Icon
    QMenu *menuIcon = self->addMenu(QStringLiteral("Tab Icon"));
    markInjected(menuIcon->menuAction());
    static const char *s_commonIcons[] = {"🦀 Rust",
                                          "🐍 Python",
                                          "📦 Node / JS",
                                          "🐹 Go",
                                          "☕ Java",
                                          "🐳 Docker",
                                          "🎨 Frontend",
                                          "⚙️ Backend",
                                          "🧪 Tests",
                                          "🚀 Launch",
                                          "🔥 Hot",
                                          "⚡ Fast",
                                          "💎 Core",
                                          "📜 Scripts"};
    for (const char *iconLabel : s_commonIcons) {
        const QString full = QString::fromUtf8(iconLabel);
        const QString glyph = full.section(QLatin1Char(' '), 0, 0);
        addTabCliAction(menuIcon, full, ctx, {QStringLiteral("set-icon")}, {glyph});
    }
    QAction *actCustomIcon = menuIcon->addAction(QStringLiteral("Custom Icon or Emoji…"));
    actCustomIcon->setEnabled(ctx.hasSession());
    QObject::connect(actCustomIcon, &QAction::triggered, [ctx]() {
        defer([ctx]() {
            bool ok = false;
            const QString text =
                QInputDialog::getText(ctx.dialogParent(), QStringLiteral("Custom Tab Icon"), QStringLiteral("Enter icon or emoji:"), QLineEdit::Normal, QString(), &ok)
                    .trimmed();
            if (ok && !text.isEmpty()) {
                runTabCli(ctx, {QStringLiteral("set-icon")}, {text});
            }
        });
    });
    addTabCliAction(menuIcon, QStringLiteral("Back to the Automatic Icon"), ctx, {QStringLiteral("set-icon"), QStringLiteral("--clear")});

    // 7. Send tab to window
    QMenu *menuSend = self->addMenu(QStringLiteral("Send Tab To…"));
    markInjected(menuSend->menuAction());

    QWidget *currentWindow = ctx.container ? ctx.container->window() : nullptr;
    const bool canDetach = ctx.container && ctx.container->count() > 1; // Konsole disables detach for the last tab
    QAction *actSendNew = menuSend->addAction(QStringLiteral("New Window (Detach)"));
    actSendNew->setEnabled(canDetach);
    QObject::connect(actSendNew, &QAction::triggered, [ctx]() {
        defer([ctx]() {
            const int idx = ctx.currentIndex();
            if (idx >= 0 && ctx.container->count() > 1) {
                // detachTab(int) is a signal of TabbedViewContainer; ViewManager handles it.
                if (QMetaObject::invokeMethod(ctx.container.data(), "detachTab", Qt::DirectConnection, Q_ARG(int, idx))) {
                    return;
                }
                qWarning("konsole-aware: emitting detachTab failed, falling back to CLI");
            }
            runTabCli(ctx, {QStringLiteral("detach")});
        });
    });

    // Mirror DetachableTabBar: allow moving the last tab only when the window has a single container.
    bool canMove = false;
    if (ctx.container && currentWindow) {
        int containersInWindow = 0;
        const auto tabWidgets = currentWindow->findChildren<QTabWidget *>();
        for (QTabWidget *t : tabWidgets) {
            if (t->inherits("Konsole::TabbedViewContainer")) {
                ++containersInWindow;
            }
        }
        canMove = containersInWindow == 1 || ctx.container->count() > 1;
    }

    int winIndex = 1;
    bool foundOtherWindow = false;
    const QWidgetList topList = QApplication::topLevelWidgets();
    for (QWidget *w : topList) {
        if (!w || w == currentWindow || !w->isVisible() || !w->inherits("Konsole::MainWindow")) {
            continue;
        }
        if (!findViewManagerOwner(w)) {
            continue;
        }
        foundOtherWindow = true;
        QString title = w->windowTitle().trimmed();
        if (title.isEmpty()) {
            title = QStringLiteral("Window %1").arg(winIndex);
        }
        QAction *a = menuSend->addAction(QStringLiteral("Window: %1").arg(title.left(35)));
        a->setEnabled(canMove && moveTabToWindowFn() != nullptr);
        const QPointer<QWidget> target(w);
        QObject::connect(a, &QAction::triggered, [ctx, target]() {
            defer([ctx, target]() {
                const int idx = ctx.currentIndex();
                MoveTabToWindowFn fn = moveTabToWindowFn();
                if (idx < 0 || !target || !fn) {
                    qWarning("konsole-aware: send-to-window aborted (tab or target window gone)");
                    return;
                }
                if (!target->inherits("Konsole::MainWindow") || !findViewManagerOwner(target.data()) || target->window() == ctx.container->window()) {
                    return;
                }
                fn(ctx.container.data(), idx, target.data());
            });
        });
        ++winIndex;
    }
    if (!foundOtherWindow) {
        QAction *aDisabled = menuSend->addAction(QStringLiteral("(No other Konsole windows open)"));
        aDisabled->setEnabled(false);
    }

    // 8. Split view
    QMenu *menuSplit = self->addMenu(QStringLiteral("Split View"));
    markInjected(menuSplit->menuAction());
    addTabCliAction(menuSplit, QStringLiteral("Split Left / Right"), ctx, {QStringLiteral("split"), QStringLiteral("left-right")});
    addTabCliAction(menuSplit, QStringLiteral("Split Top / Bottom"), ctx, {QStringLiteral("split"), QStringLiteral("top-bottom")});
    addTabCliAction(menuSplit, QStringLiteral("Equalize Split Sizes"), ctx, {QStringLiteral("equalize")});

    // 9. Organize
    QMenu *menuOrg = self->addMenu(QStringLiteral("Organize Tabs"));
    markInjected(menuOrg->menuAction());
    addTabCliAction(menuOrg, QStringLiteral("By Status (Pinned → Busy → Done → Idle)"), ctx, {QStringLiteral("organize"), QStringLiteral("--by"), QStringLiteral("status")});
    addTabCliAction(menuOrg, QStringLiteral("By Group"), ctx, {QStringLiteral("organize"), QStringLiteral("--by"), QStringLiteral("group")});
    addTabCliAction(menuOrg, QStringLiteral("By Name"), ctx, {QStringLiteral("organize"), QStringLiteral("--by"), QStringLiteral("name")});
    addTabCliAction(menuOrg,
                    QStringLiteral("By Project (agents side by side)"),
                    ctx,
                    {QStringLiteral("organize"), QStringLiteral("--by"), QStringLiteral("project")});
}

using QMenuExecFn = QAction *(*)(QMenu *, const QPoint &, QAction *);

QMenuExecFn realExec()
{
    static QMenuExecFn fn = nullptr;
    if (!fn) {
        fn = reinterpret_cast<QMenuExecFn>(dlsym(RTLD_NEXT, "_ZN5QMenu4execERK6QPointP7QAction"));
    }
    return fn;
}

} // namespace

// Interposes QMenu::exec(const QPoint &p, QAction *action = nullptr)
extern "C" __attribute__((visibility("default"))) QAction *_ZN5QMenu4execERK6QPointP7QAction(QMenu *self, const QPoint &p, QAction *action)
{
    const QMenuExecFn real = realExec();
    if (!real) {
        return nullptr;
    }
    if (!self || !isKonsoleProcess()) {
        return real(self, p, action);
    }

    // Structural check: the menu must belong to the tab bar of a Konsole::TabbedViewContainer.
    auto *tabBar = qobject_cast<QTabBar *>(self->parentWidget());
    auto *tw = tabBar ? qobject_cast<QTabWidget *>(tabBar->parentWidget()) : nullptr;
    if (!tw || !tw->inherits("Konsole::TabbedViewContainer") || tw->tabBar() != tabBar || !looksLikeKonsoleTabMenu(self)) {
        return real(self, p, action);
    }

    tabBar->setElideMode(Qt::ElideRight);
    tabBar->setUsesScrollButtons(true);

    // Drop anything left over from a previous display (including submenus).
    removeInjected(self, false);

    // Konsole computes the index as tabBar()->tabAt(point) and execs at mapToGlobal(point).
    const int tabIndex = tabBar->tabAt(tabBar->mapFromGlobal(p));
    if (tabIndex < 0 || tabIndex >= tw->count()) {
        return real(self, p, action);
    }

    TabCtx ctx;
    ctx.container = tw;
    ctx.page = tw->widget(tabIndex);
    ctx.sessionId = sessionIdForTab(tw, tabIndex);
    ctx.service = konsoleService();

    injectActions(self, ctx);

    const QPointer<QMenu> guard(self);
    QAction *result = real(self, p, action);
    if (guard) {
        // Deferred so `result` stays valid for the caller.
        removeInjected(guard.data(), true);
    }
    return result;
}
