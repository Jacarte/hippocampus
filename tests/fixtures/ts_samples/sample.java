public class Main {
    interface Greeter {
        String greet(String name);
    }

    enum Color {
        RED, GREEN, BLUE
    }

    public String hello(String name) {
        return "Hello, " + name;
    }
}
