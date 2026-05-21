trait Greeter {
    fn greet(&self) -> String;
}

type Alias = String;

enum Color {
    Red,
    Green,
    Blue,
}

struct Dog {
    name: String,
}

impl Dog {
    fn bark(&self) -> String {
        String::from("Woof")
    }
}

fn hello(name: &str) -> String {
    format!("Hello, {}", name)
}
