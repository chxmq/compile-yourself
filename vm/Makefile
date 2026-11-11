CC ?= cc
CFLAGS ?= -O2 -Wall -Wextra

sqvm: sqvm.c
	$(CC) $(CFLAGS) -o $@ $<

clean:
	rm -f sqvm
.PHONY: clean
