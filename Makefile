
.PHONY: all
all:
	./scripts/lyrics.py -o songbook.pdf songs

.PHONY: clean
clean:
	rm -f songbook.pdf
