MUSLLIBC = $(shell gcc -dumpmachine | grep musl | wc -l)

TARGET	= pegasus-cluster pegasus-kickstart

# only include pegasus-keg on glibc systems
ifeq (0,${MUSLLIBC})
    TARGET += pegasus-keg
endif

MARCH   = $(shell uname -m | tr '[A-Z]' '[a-z]')

ifndef ${prefix}
prefix = $(PEGASUS_HOME)
endif

.ONESHELL:
all install:
	for i in $(TARGET); do \
	    if [ -d $$i ]; then \
		$(MAKE) -C $$i $@ || exit 1; \
	    fi; \
	done

.ONESHELL:
clean distclean:
	for i in $(TARGET); do \
	    if [ -d $$i ]; then \
		$(MAKE) -C $$i $@ || true; \
	    fi; \
	done
