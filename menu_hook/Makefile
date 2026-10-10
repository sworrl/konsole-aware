CXX ?= g++
CXXFLAGS ?= -O2 -Wall -Wextra
# Required for a preloadable shared library no matter what CXXFLAGS the caller (e.g. rpmbuild) passes.
REQUIRED_FLAGS := -fPIC -std=c++17
QT_MODULES := Qt6Widgets Qt6DBus
# Export only the entry points marked default visibility in the sources.
VISIBILITY_FLAGS := -fvisibility=hidden -fvisibility-inlines-hidden
QT_CFLAGS := $(shell pkg-config --cflags $(QT_MODULES))
QT_LIBS := $(shell pkg-config --libs $(QT_MODULES)) -ldl

# The loader is what Konsole preloads; it loads the module and reloads it when it changes.
LOADER := libkonsole_aware_menu.so
MODULE := libkonsole_aware_module.so

all: $(LOADER) $(MODULE)

$(LOADER): konsole_aware_loader.cpp Makefile
	$(CXX) $(CXXFLAGS) $(REQUIRED_FLAGS) $(VISIBILITY_FLAGS) -shared $(LDFLAGS) $(QT_CFLAGS) $< -o $@ $(QT_LIBS)

$(MODULE): konsole_aware_module.cpp Makefile
	$(CXX) $(CXXFLAGS) $(REQUIRED_FLAGS) $(VISIBILITY_FLAGS) -shared $(LDFLAGS) $(QT_CFLAGS) $< -o $@ $(QT_LIBS)

clean:
	rm -f $(LOADER) $(MODULE)

.PHONY: all clean
