# Exercise 2 — DNI letter
dni_letters: str = "TRWAGMYFPDXBNJZSQVHLCKE"

dni: str = input("DNI (8 cifras y letra, p. ej. 12345678Z): ")

# Normalize: no surrounding spaces and the letter in upper case
clean_dni: str = dni.strip().upper()

# Split the numeric part (a slice) and the letter (one character)
number: int = int(clean_dni[0:8])
entered_letter: str = clean_dni[8]

# The actual calculation: the remainder of dividing by 23 is the index of the letter
remainder: int = number % 23
correct_letter: str = dni_letters[remainder]
is_correct: bool = entered_letter == correct_letter

print()
print(f"Número:            {number}")
print(f"Resto entre 23:    {remainder}")
print(f"Letra correcta:    {correct_letter}")
print(f"Letra introducida: {entered_letter}")
print(f"DNI válido:        {is_correct}")
