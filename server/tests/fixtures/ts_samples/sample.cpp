#include <string>

typedef int MyInt;

enum class Color {
    Red,
    Green,
    Blue
};

struct Point {
    int x;
    int y;
};

class Animal {
public:
    virtual std::string speak() {
        return "...";
    }
};

void greet(const std::string& name) {
    // greet
}
