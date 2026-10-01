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

void injectActions(QMenu *self, const TabCtx &ctx)
{
    QAction *sep = self->addSeparator();
    markInjected(sep);

    // 1. Anchor / pin
    markInjected(addTabCliAction(self, QStringLiteral("📌 Anchor / Pin Tab"), ctx, {QStringLiteral("anchor")}));
    markInjected(addTabCliAction(self, QStringLiteral("📍 Unpin Tab"), ctx, {QStringLiteral("unanchor")}));

    // 2. Snap project tabs together
    markInjected(addTabCliAction(self, QStringLiteral("🧲 Snap Project Tabs Together"), ctx, {QStringLiteral("snap")}));

    // 3. Colour
    QMenu *menuColor = self->addMenu(QStringLiteral("🎨 Tab Color"));
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
    QMenu *menuAgentColors = menuColor->addMenu(QStringLiteral("🤖 Agent Brand Colors"));
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
    QAction *actCustomColor = menuColor->addAction(QStringLiteral("🎨 Custom Color..."));
    actCustomColor->setEnabled(ctx.hasSession());
    QObject::connect(actCustomColor, &QAction::triggered, [ctx]() {
        defer([ctx]() {
            const QColor chosen = QColorDialog::getColor(Qt::white, ctx.dialogParent(), QStringLiteral("Select Tab Color"));
            if (chosen.isValid()) {
                runTabCli(ctx, {QStringLiteral("set-color"), chosen.name()});
            }
        });
    });

    // 4. Group
    QMenu *menuGroup = self->addMenu(QStringLiteral("🏷️ Assign Group"));
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
    QAction *actCustomGroup = menuGroup->addAction(QStringLiteral("✏️ Custom Group..."));
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
    addTabCliAction(menuGroup, QStringLiteral("❌ Remove from Group"), ctx, {QStringLiteral("group"), QStringLiteral("remove")});

    // 5. Project & job context
    QMenu *menuJob = self->addMenu(QStringLiteral("📂 Project & Job Context"));
    markInjected(menuJob->menuAction());
    QAction *actSetJob = menuJob->addAction(QStringLiteral("✏️ Set Custom Job Name..."));
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
    addTabCliAction(menuJob, QStringLiteral("🔄 Reset Job Name to Auto"), ctx, {QStringLiteral("set-job"), QStringLiteral("--clear")});

    // 6. Icon
    QMenu *menuIcon = self->addMenu(QStringLiteral("🎯 Tab Icon"));
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
    QAction *actCustomIcon = menuIcon->addAction(QStringLiteral("✏️ Custom Icon / Emoji..."));
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
    addTabCliAction(menuIcon, QStringLiteral("🔄 Reset Icon to Auto"), ctx, {QStringLiteral("set-icon"), QStringLiteral("--clear")});

    // 7. Send tab to window
    QMenu *menuSend = self->addMenu(QStringLiteral("🪟 Send Tab To..."));
    markInjected(menuSend->menuAction());

    QWidget *currentWindow = ctx.container ? ctx.container->window() : nullptr;
    const bool canDetach = ctx.container && ctx.container->count() > 1; // Konsole disables detach for the last tab
    QAction *actSendNew = menuSend->addAction(QStringLiteral("➕ New Window (Detach)"));
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
        QAction *a = menuSend->addAction(QStringLiteral("🪟 Window: %1").arg(title.left(35)));
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
    QMenu *menuSplit = self->addMenu(QStringLiteral("✂️ Split View"));
    markInjected(menuSplit->menuAction());
    addTabCliAction(menuSplit, QStringLiteral("⬌ Split Left / Right"), ctx, {QStringLiteral("split"), QStringLiteral("left-right")});
    addTabCliAction(menuSplit, QStringLiteral("⬍ Split Top / Bottom"), ctx, {QStringLiteral("split"), QStringLiteral("top-bottom")});
    addTabCliAction(menuSplit, QStringLiteral("⚖️ Equalize Split Sizes"), ctx, {QStringLiteral("equalize")});

    // 9. Organize
    QMenu *menuOrg = self->addMenu(QStringLiteral("🧹 Organize Tabs"));
    markInjected(menuOrg->menuAction());
    addTabCliAction(menuOrg, QStringLiteral("📊 By Status (Pinned → Busy → Done → Idle)"), ctx, {QStringLiteral("organize"), QStringLiteral("--by"), QStringLiteral("status")});
    addTabCliAction(menuOrg, QStringLiteral("🏷️ By Group"), ctx, {QStringLiteral("organize"), QStringLiteral("--by"), QStringLiteral("group")});
    addTabCliAction(menuOrg, QStringLiteral("🔤 By Name"), ctx, {QStringLiteral("organize"), QStringLiteral("--by"), QStringLiteral("name")});
    addTabCliAction(menuOrg,
                    QStringLiteral("📂 By Project (agents side by side)"),
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
