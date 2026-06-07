CC = gcc
CFLAGS = -Wall -O2 -I. -I/usr/include/freetype2
LDFLAGS = -lX11 -lXft -lXrender -lm -lpng -lfontconfig

TARGET = lut_viewer

all: $(TARGET)

$(TARGET): viewer.c lut_engine.c image_io.c
	$(CC) $(CFLAGS) -o $@ viewer.c lut_engine.c image_io.c $(LDFLAGS)

clean:
	rm -f $(TARGET)

.PHONY: clean all
