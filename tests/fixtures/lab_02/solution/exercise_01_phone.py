# Exercise 1 — Phone number validator
phone: str = input("Número de teléfono: ")

# Normalize: remove spaces and Spain's international prefix
no_spaces: str = phone.replace(" ", "")
has_prefix: bool = no_spaces.startswith("+34")
number: str = no_spaces.replace("+34", "")

# Independent conditions (each one is a bool)
length_ok: bool = len(number) == 9
digits_only: bool = number.isdigit()
first_digit: str = number[0]
first_digit_ok: bool = first_digit in "6789"
valid: bool = length_ok and digits_only and first_digit_ok

# Line type
is_mobile: bool = first_digit in "67"
is_landline: bool = first_digit in "89"

# "612 345 678" format with three slices
formatted: str = f"{number[0:3]} {number[3:6]} {number[6:9]}"

print()
print("--- Análisis del número ---")
print(f"Prefijo +34:          {has_prefix}")
print(f"Número sin prefijo:   {number}")
print(f"Tiene 9 cifras:       {length_ok}")
print(f"Solo cifras:          {digits_only}")
print(f"Empieza por 6/7/8/9:  {first_digit_ok}")
print(f"Válido:               {valid}")
print(f"Es móvil:             {is_mobile}")
print(f"Es fijo:              {is_landline}")
print(f"Formateado:           {formatted}")
