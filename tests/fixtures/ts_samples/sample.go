package main

import "fmt"

type Animal interface {
	Speak() string
}

type Dog struct {
	Name string
}

type DogAlias = Dog

func (d Dog) Speak() string {
	return "Woof"
}

func Greet(name string) {
	fmt.Println("Hello", name)
}
