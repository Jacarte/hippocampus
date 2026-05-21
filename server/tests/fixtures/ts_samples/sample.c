#include <stdio.h>

typedef struct {
    int x;
    int y;
} Point;

typedef int MyInt;

typedef enum {
    RED,
    GREEN,
    BLUE
} Color;

void greet(const char* name) {
    printf("Hello, %s\n", name);
}
