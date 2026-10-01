#define _GNU_SOURCE
#include <dlfcn.h>
#include <QAction>
#include <QApplication>
#include <QColor>
#include <QColorDialog>
#include <QCursor>
#include <QIcon>
#include <QInputDialog>
#include <QLineEdit>
#include <QMenu>
#include <QMetaObject>
#include <QPoint>
#include <QProcess>
#include <QTabBar>
#include <QTabWidget>
#include <QWidget>
#include <QVariant>
#include <vector>

// Function signatures for Konsole's internal symbols in libkonsoleprivate.so
typedef void (*MoveTabToWindowFn)(QWidget* container, int tabIndex, QWidget* targetWindow);
typedef void (*DetachTabFn)(QWidget* container, int tabIndex);
typedef void (*SetColorFn)(QWidget* container, int tabIndex, const QColor& color);
typedef void (*RemoveColorFn)(QWidget* container, int tabIndex);

static MoveTabToWindowFn s_moveTabToWindow = nullptr;
static DetachTabFn s_detachTab = nullptr;
static SetColorFn s_setColor = nullptr;
static RemoveColorFn s_removeColor = nullptr;

static void initKonsoleSymbols() {
    static bool s_initialized = false;
    if (s_initialized) return;
    s_initialized = true;

    s_moveTabToWindow = (MoveTabToWindowFn)dlsym(RTLD_DEFAULT, "_ZN7Konsole19TabbedViewContainer15moveTabToWindowEiP7QWidget");
    s_detachTab = (DetachTabFn)dlsym(RTLD_DEFAULT, "_ZN7Konsole19TabbedViewContainer9detachTabEi");
    s_setColor = (SetColorFn)dlsym(RTLD_DEFAULT, "_ZN7Konsole19TabbedViewContainer8setColorEiRK6QColor");
    s_removeColor = (RemoveColorFn)dlsym(RTLD_DEFAULT, "_ZN7Konsole19TabbedViewContainer11removeColorEi");
}

static void runCli(const QStringList& args) {
    QProcess::startDetached("konsole-aware", args);
}

// Intercept QMenu::exec(const QPoint &p, QAction *action = nullptr)
typedef QAction* (*QMenuExecFn)(QMenu*, const QPoint&, QAction*);
static QMenuExecFn s_real_exec = nullptr;

extern "C" QAction* _ZN5QMenu4execERK6QPointP7QAction(QMenu* self, const QPoint& p, QAction* action) {
    if (!s_real_exec) {
        s_real_exec = (QMenuExecFn)dlsym(RTLD_NEXT, "_ZN5QMenu4execERK6QPointP7QAction");
    }

    if (!self) {
        return nullptr;
    }

    // Check if this is Konsole's tab bar context menu
    bool isTabMenu = false;
    for (QAction* act : self->actions()) {
        if (!act) continue;
        QString text = act->text();
        if (text.contains("Detach Tab", Qt::CaseInsensitive) ||
            text.contains("Configure or Rename Tab", Qt::CaseInsensitive)) {
            isTabMenu = true;
            break;
        }
    }

    if (!isTabMenu) {
        return s_real_exec ? s_real_exec(self, p, action) : nullptr;
    }

    initKonsoleSymbols();

    // Find parent container and QTabBar
    QWidget* container = self->parentWidget();
    QTabBar* tabBar = nullptr;
    if (container) {
        tabBar = container->findChild<QTabBar*>();
        if (!tabBar && qobject_cast<QTabBar*>(container)) {
            tabBar = qobject_cast<QTabBar*>(container);
        }
    }

    int tabIndex = -1;
    if (tabBar) {
        QPoint localPt = tabBar->mapFromGlobal(p);
        tabIndex = tabBar->tabAt(localPt);
        if (tabIndex < 0) {
            tabIndex = tabBar->currentIndex();
        }
    }

    // Clean up any previously injected actions from prior menu displays
    QList<QAction*> currentActions = self->actions();
    for (QAction* act : currentActions) {
        if (act && act->property("konsole_aware_injected").toBool()) {
            self->removeAction(act);
            delete act;
        }
    }

    // Add Separator before our custom sections
    QAction* sep = self->addSeparator();
    sep->setProperty("konsole_aware_injected", true);

    // 1. PIN / ANCHOR TAB
    QAction* actPin = self->addAction("📌 Anchor / Pin Tab");
    actPin->setProperty("konsole_aware_injected", true);
    QObject::connect(actPin, &QAction::triggered, [container, tabIndex]() {
        runCli(QStringList() << "anchor");
    });

    QAction* actUnpin = self->addAction("📍 Unpin Tab");
    actUnpin->setProperty("konsole_aware_injected", true);
    QObject::connect(actUnpin, &QAction::triggered, [container, tabIndex]() {
        runCli(QStringList() << "unanchor");
    });

    // 2. TAB COLOR SUBMENU
    QMenu* menuColor = self->addMenu("🎨 Tab Color");
    menuColor->menuAction()->setProperty("konsole_aware_injected", true);

    struct ColorChoice {
        const char* label;
        const char* hex;
        const char* name;
    };
    static const ColorChoice s_colors[] = {
        {"🟢 Emerald (Done / Success)", "#10b981", "emerald"},
        {"🟡 Amber (Busy / Working)", "#f59e0b", "amber"},
        {"🔴 Red (Error / Alert)", "#ef4444", "red"},
        {"🟣 Purple (Waiting / Attention)", "#8b5cf6", "purple"},
        {"🔵 Blue (Idle / Shell)", "#3b82f6", "blue"},
        {"⚪ Default / Reset Color", "#000000", "reset"},
    };

    for (const auto& c : s_colors) {
        QAction* a = menuColor->addAction(QString::fromUtf8(c.label));
        QString hexStr = QString::fromUtf8(c.hex);
        QString colorName = QString::fromUtf8(c.name);
        QObject::connect(a, &QAction::triggered, [container, tabIndex, hexStr, colorName]() {
            if (container && tabIndex >= 0) {
                if (colorName == "reset") {
                    if (s_removeColor) s_removeColor(container, tabIndex);
                } else if (s_setColor) {
                    s_setColor(container, tabIndex, QColor(hexStr));
                }
            }
            runCli(QStringList() << "set-color" << hexStr);
        });
    }

    QAction* actCustomColor = menuColor->addAction("🎨 Custom Color...");
    QObject::connect(actCustomColor, &QAction::triggered, [container, tabIndex]() {
        QColor chosen = QColorDialog::getColor(Qt::white, container, "Select Tab Color");
        if (chosen.isValid()) {
            if (container && tabIndex >= 0 && s_setColor) {
                s_setColor(container, tabIndex, chosen);
            }
            runCli(QStringList() << "set-color" << chosen.name());
        }
    });

    // 3. TAB GROUP SUBMENU
    QMenu* menuGroup = self->addMenu("🏷️ Assign Group");
    menuGroup->menuAction()->setProperty("konsole_aware_injected", true);

    struct GroupChoice {
        const char* label;
        const char* name;
        const char* color;
        const char* icon;
    };
    static const GroupChoice s_groups[] = {
        {"🤖 Agents", "agents", "#8b5cf6", "🤖"},
        {"🎨 Frontend", "frontend", "#3b82f6", "🎨"},
        {"⚙️ Backend", "backend", "#10b981", "⚙️"},
        {"📊 Data", "data", "#eab308", "📊"},
        {"☁️ Infrastructure", "infra", "#06b6d4", "☁️"},
    };

    for (const auto& g : s_groups) {
        QAction* a = menuGroup->addAction(QString::fromUtf8(g.label));
        QString gName = QString::fromUtf8(g.name);
        QString gColor = QString::fromUtf8(g.color);
        QString gIcon = QString::fromUtf8(g.icon);
        QObject::connect(a, &QAction::triggered, [gName, gColor, gIcon]() {
            runCli(QStringList() << "group" << "set" << gName << "--color" << gColor << "--icon" << gIcon);
        });
    }

    QAction* actCustomGroup = menuGroup->addAction("✏️ Custom Group...");
    QObject::connect(actCustomGroup, &QAction::triggered, [container]() {
        bool ok = false;
        QString name = QInputDialog::getText(container, "Custom Tab Group", "Enter group name:", QLineEdit::Normal, "", &ok);
        if (ok && !name.trimmed().isEmpty()) {
            runCli(QStringList() << "group" << "set" << name.trimmed());
        }
    });

    QAction* actRemoveGroup = menuGroup->addAction("❌ Remove from Group");
    QObject::connect(actRemoveGroup, &QAction::triggered, []() {
        runCli(QStringList() << "group" << "remove");
    });

    // 4. PROJECT & JOB CONTEXT
    QMenu* menuJob = self->addMenu("📂 Project & Job Context");
    menuJob->menuAction()->setProperty("konsole_aware_injected", true);

    QAction* actSetJob = menuJob->addAction("✏️ Set Custom Job Name...");
    QObject::connect(actSetJob, &QAction::triggered, [container]() {
        bool ok = false;
        QString text = QInputDialog::getText(container, "Set Job Name", "Enter permanent project or job name:", QLineEdit::Normal, "", &ok);
        if (ok && !text.trimmed().isEmpty()) {
            runCli(QStringList() << "set-job" << text.trimmed());
        }
    });

    QAction* actClearJob = menuJob->addAction("🔄 Reset Job Name to Auto");
    QObject::connect(actClearJob, &QAction::triggered, []() {
        runCli(QStringList() << "set-job" << "--clear");
    });

    // 5. ICON SUBMENU
    QMenu* menuIcon = self->addMenu("🎯 Tab Icon");
    menuIcon->menuAction()->setProperty("konsole_aware_injected", true);

    static const char* s_commonIcons[] = {
        "🦀 Rust", "🐍 Python", "📦 Node / JS", "🐹 Go", "☕ Java",
        "🐳 Docker", "🎨 Frontend", "⚙️ Backend", "🧪 Tests",
        "🚀 Launch", "🔥 Hot", "⚡ Fast", "💎 Core", "📜 Scripts"
    };

    for (const char* iconLabel : s_commonIcons) {
        QString fullStr = QString::fromUtf8(iconLabel);
        QString glyph = fullStr.split(" ").first();
        QAction* a = menuIcon->addAction(fullStr);
        QObject::connect(a, &QAction::triggered, [glyph]() {
            runCli(QStringList() << "set-icon" << glyph);
        });
    }

    QAction* actCustomIcon = menuIcon->addAction("✏️ Custom Icon / Emoji...");
    QObject::connect(actCustomIcon, &QAction::triggered, [container]() {
        bool ok = false;
        QString text = QInputDialog::getText(container, "Custom Tab Icon", "Enter icon or emoji:", QLineEdit::Normal, "", &ok);
        if (ok && !text.trimmed().isEmpty()) {
            runCli(QStringList() << "set-icon" << text.trimmed());
        }
    });

    QAction* actClearIcon = menuIcon->addAction("🔄 Reset Icon to Auto");
    QObject::connect(actClearIcon, &QAction::triggered, []() {
        runCli(QStringList() << "set-icon" << "--clear");
    });

    // 6. SEND TAB TO ANOTHER WINDOW
    QMenu* menuSend = self->addMenu("🪟 Send Tab To...");
    menuSend->menuAction()->setProperty("konsole_aware_injected", true);

    // Option A: New Window
    QAction* actSendNew = menuSend->addAction("➕ New Window (Detach)");
    QObject::connect(actSendNew, &QAction::triggered, [container, tabIndex]() {
        if (container && tabIndex >= 0 && s_detachTab) {
            s_detachTab(container, tabIndex);
        } else {
            runCli(QStringList() << "detach");
        }
    });

    // Option B: Other open Konsole windows
    QWidget* currentWindow = container ? container->window() : nullptr;
    QWidgetList topList = QApplication::topLevelWidgets();
    int winIndex = 1;
    bool foundOtherWindow = false;

    for (QWidget* w : topList) {
        if (!w || w == currentWindow) continue;
        if (!w->isVisible()) continue;

        const char* cls = w->metaObject()->className();
        if (QString(cls).contains("MainWindow", Qt::CaseInsensitive)) {
            foundOtherWindow = true;
            QString title = w->windowTitle().trimmed();
            if (title.isEmpty()) {
                title = QString("Window %1").arg(winIndex);
            }
            QString label = QString("🪟 Window: %1").arg(title.left(35));
            QAction* a = menuSend->addAction(label);
            QWidget* targetWin = w;

            QObject::connect(a, &QAction::triggered, [container, tabIndex, targetWin]() {
                if (container && tabIndex >= 0 && targetWin && s_moveTabToWindow) {
                    s_moveTabToWindow(container, tabIndex, targetWin);
                }
            });
            winIndex++;
        }
    }

    if (!foundOtherWindow) {
        QAction* aDisabled = menuSend->addAction("(No other Konsole windows open)");
        aDisabled->setEnabled(false);
    }

    // 7. SPLIT VIEW SUBMENU
    QMenu* menuSplit = self->addMenu("✂️ Split View");
    menuSplit->menuAction()->setProperty("konsole_aware_injected", true);

    QAction* actSplitLR = menuSplit->addAction("⬌ Split Left / Right");
    QObject::connect(actSplitLR, &QAction::triggered, []() {
        runCli(QStringList() << "split" << "left-right");
    });

    QAction* actSplitTB = menuSplit->addAction("⬍ Split Top / Bottom");
    QObject::connect(actSplitTB, &QAction::triggered, []() {
        runCli(QStringList() << "split" << "top-bottom");
    });

    QAction* actEqualize = menuSplit->addAction("⚖️ Equalize Split Sizes");
    QObject::connect(actEqualize, &QAction::triggered, []() {
        runCli(QStringList() << "equalize");
    });

    // 8. ORGANIZE TABS SUBMENU
    QMenu* menuOrg = self->addMenu("🧹 Organize Tabs");
    menuOrg->menuAction()->setProperty("konsole_aware_injected", true);

    QAction* actOrgStatus = menuOrg->addAction("📊 By Status (Pinned → Busy → Done → Idle)");
    QObject::connect(actOrgStatus, &QAction::triggered, []() {
        runCli(QStringList() << "organize" << "--by" << "status");
    });

    QAction* actOrgGroup = menuOrg->addAction("🏷️ By Group");
    QObject::connect(actOrgGroup, &QAction::triggered, []() {
        runCli(QStringList() << "organize" << "--by" << "group");
    });

    QAction* actOrgName = menuOrg->addAction("🔤 By Name");
    QObject::connect(actOrgName, &QAction::triggered, []() {
        runCli(QStringList() << "organize" << "--by" << "name");
    });

    return s_real_exec ? s_real_exec(self, p, action) : nullptr;
}
