CXX ?= g++
CXXFLAGS ?= -O2 -fPIC -std=c++17 -Wall -Wextra
QT_MODULES := Qt6Widgets Qt6DBus
# Export only the QMenu::exec interposer (marked default visibility in source).
VISIBILITY_FLAGS := -fvisibility=hidden -fvisibility-inlines-hidden
QT_CFLAGS := $(shell pkg-config --cflags $(QT_MODULES))
QT_LIBS := $(shell pkg-config --libs $(QT_MODULES)) -ldl

TARGET := libkonsole_aware_menu.so

all: $(TARGET)

$(TARGET): konsole_aware_menu.cpp Makefile
	$(CXX) $(CXXFLAGS) $(VISIBILITY_FLAGS) -shared $(QT_CFLAGS) $< -o $@ $(QT_LIBS)

install: $(TARGET)
	mkdir -p $(HOME)/.local/lib
	cp $(TARGET) $(HOME)/.local/lib/$(TARGET)

clean:
	rm -f $(TARGET)

.PHONY: all install clean
