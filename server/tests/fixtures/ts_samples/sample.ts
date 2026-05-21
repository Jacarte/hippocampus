interface Greeter {
  greet(name: string): string;
}

type Alias = string;

enum Color {
  Red,
  Green,
  Blue,
}

class Dog implements Greeter {
  greet(name: string): string {
    return "Hello, " + name;
  }
}

function hello(name: string): string {
  return "Hello, " + name;
}
