# Ejercicio 2 — Media
with open("data/numbers.txt", encoding="utf-8") as file:
    numbers: list[float] = [float(line) for line in file if line.strip()]
average: float = sum(numbers) / len(numbers)
print(f"Media: {average:.2f}")
