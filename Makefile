
PREFIX ?= /usr/local
BINDIR ?= $(PREFIX)/bin
ETCDIR ?= /etc/gh2lp

all:

install:
	mkdir -p $(DESTDIR)$(BINDIR)
	cp gh2lp.py $(DESTDIR)$(BINDIR)/gh2lp
	mkdir -p $(DESTDIR)$(ETCDIR)
	cp -pn yavdr.conf $(DESTDIR)$(ETCDIR)

