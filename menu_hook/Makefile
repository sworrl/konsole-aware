CXX ?= g++
CXXFLAGS ?= -O2 -fPIC -std=c++17 -Wall -Wextra
QT_CFLAGS := $(shell pkg-config --cflags Qt6Widgets)
QT_LIBS := $(shell pkg-config --libs Qt6Widgets) -ldl

TARGET := libkonsole_aware_menu.so

all: $(TARGET)

$(TARGET): konsole_aware_menu.cpp
	$(CXX) $(CXXFLAGS) -shared $(QT_CFLAGS) $< -o $@ $(QT_LIBS)

install: $(TARGET)
	mkdir -p $(HOME)/.local/lib
	cp $(TARGET) $(HOME)/.local/lib/$(TARGET)

clean:
	rm -f $(TARGET)

.PHONY: all install clean
