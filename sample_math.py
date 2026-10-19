"""Boilerplate math utilities for bug-fixing exercises.

This file intentionally contains no bug so the user can add one manually.
"""


def add(left: float, right: float) -> float:
    return left + right


def subtract(left: float, right: float) -> float:
    return left - right


def multiply(left: float, right: float) -> float:
    return left * right


def divide(left: float, right: float) -> float:
    return left / right


def calculate(operation: str, left: float, right: float) -> float:
    operations = {
        "add": add,
        "subtract": subtract,
        "multiply": multiply,
        "divide": divide,
    }

    try:
        return operations[operation](left, right)
    except KeyError as exc:
        raise ValueError(f"Unsupported operation: {operation}") from exc


if __name__ == "__main__":
    print(calculate("add", 5, 3))
    print(calculate("subtract", 5, 3))
    print(calculate("multiply", 5, 3))
    print(calculate("divide", 6, 3))
