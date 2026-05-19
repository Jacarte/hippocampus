module Greetable
  def greet(name)
    "Hello, #{name}"
  end
end

class Animal
  include Greetable

  def speak
    "..."
  end
end

def hello(name)
  "Hello, #{name}"
end
