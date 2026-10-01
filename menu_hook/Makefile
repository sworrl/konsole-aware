CXX ?= g++
CXXFLAGS ?= -O2 -Wall -Wextra
# Required for a preloadable shared library no matter what CXXFLAGS the caller (e.g. rpmbuild) passes.
REQUIRED_FLAGS := -fPIC -std=c++17
QT_MODULES := Qt6Widgets Qt6DBus
# Export only the QMenu::exec interposer (marked default visibility in source).
VISIBILITY_FLAGS := -fvisibility=hidden -fvisibility-inlines-hidden
QT_CFLAGS := $(shell pkg-config --cflags $(QT_MODULES))
QT_LIBS := $(shell pkg-config --libs $(QT_MODULES)) -ldl

TARGET := libkonsole_aware_menu.so

all: $(TARGET)

$(TARGET): konsole_aware_menu.cpp Makefile
	$(CXX) $(CXXFLAGS) $(REQUIRED_FLAGS) $(VISIBILITY_FLAGS) -shared $(LDFLAGS) $(QT_CFLAGS) $< -o $@ $(QT_LIBS)

install: $(TARGET)
	mkdir -p $(HOME)/.local/lib
	cp $(TARGET) $(HOME)/.local/lib/$(TARGET)

clean:
	rm -f $(TARGET)

.PHONY: all install clean
